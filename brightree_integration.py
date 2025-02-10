import json
import re
import time
from urllib.parse import unquote
from datetime import datetime
import aiohttp
import urllib.parse
from bs4 import BeautifulSoup
from typing import Any, Union
from fake_useragent import UserAgent
from submodule_integrations.brightree.models.models import BasePatient, SalesOrder
from submodule_integrations.models.integration import Integration
from submodule_integrations.utils.errors import (
    IntegrationAuthError,
    IntegrationAPIError,
)


class BrightreeIntegration(Integration):
    def __init__(self, user_agent: str = UserAgent().random):
        super().__init__("brightree")
        self.network_requester = None
        self.user_agent = user_agent
        self.url = "https://brightree.net"
        self.headers = None
        self.cookies = None
        self.session_id = None
        self.session_salt = None
        self.session_protected = None
        self.submission_id = None

    async def initialize(self, tokens: str, network_requester=None):
        self.network_requester = network_requester
        self.headers = {
            'Host': 'brightree.net',
            "User-Agent": self.user_agent,
            "Cookie": tokens,
            "Accept-Encoding": "gzip",
            "Accept": "*/*"
        }

    async def _make_request(self, method: str, url: str, **kwargs) -> str:
        """
        Helper method to handle network requests using either custom requester or aiohttp.
        Prefers automatic redirects but falls back to manual handling if needed.
        """
        if self.network_requester:
            response = await self.network_requester.request(
                method, url, process_response=self._handle_response, **kwargs
            )
            return response

        max_redirects = kwargs.pop('max_redirects', 5)

        async with aiohttp.ClientSession() as session:
            # First try with automatic redirects
            try:
                async with session.request(method, url, allow_redirects=True, **kwargs) as response:
                    if response.status == 200:
                        return await self._handle_response(response)

                    # If we still get a redirect status, fall back to manual handling
                    if response.status in (301, 302, 303, 307, 308):
                        print("Automatic redirect failed, handling manually")
                        return await self._handle_manual_redirect(session, method, url, max_redirects, **kwargs)

                    if response.status == 404:
                        raise IntegrationAuthError(
                            message="Expired tokens",
                            status_code=response.status,
                        )

                    return await self._handle_response(response)

            except aiohttp.ClientError as e:
                print(f"Automatic redirect failed with error: {e}, attempting manual redirect")
                return await self._handle_manual_redirect(session, method, url, max_redirects, **kwargs)

    async def _handle_manual_redirect(self, session, method: str, url: str, max_redirects: int, **kwargs) -> str:
        """Handle redirects manually when automatic redirects fail"""
        redirect_count = 0
        current_url = url
        current_method = method

        while redirect_count < max_redirects:
            async with session.request(current_method, current_url, allow_redirects=False, **kwargs) as response:
                if response.status in (301, 302, 303, 307, 308):
                    redirect_count += 1
                    next_url = response.headers.get("Location")

                    if not next_url:
                        raise IntegrationAPIError(
                            self.integration_name,
                            f"Received redirect status {response.status} but no Location header",
                        )

                    # Handle relative URLs
                    if next_url.startswith('/'):
                        parsed_url = urllib.parse.urlparse(current_url)
                        next_url = f"{parsed_url.scheme}://{parsed_url.netloc}{next_url}"

                    print(f"Following manual redirect {redirect_count}/{max_redirects}: {next_url}")
                    current_url = next_url

                    # For 303, always use GET for the redirect
                    if response.status == 303:
                        current_method = "GET"

                    continue

                return await self._handle_response(response)

        raise IntegrationAPIError(
            self.integration_name,
            f"Too many redirects (max: {max_redirects})",
        )

    async def _handle_response(
            self, response: aiohttp.ClientResponse
    ) -> Union[str, Any]:
        if response.status == 200 or response.ok:
            r_text = await response.text()

            if "<!DOCTYPE html>" in r_text:
                if "Access is denied" in r_text or "Brightree Login" in r_text:
                    raise IntegrationAuthError(
                        message="Unauthorized",
                        status_code=401
                    )

            return await response.text()

        status_code = response.status
        # do things with fail status codes
        if 400 <= status_code < 500:
            # potential auth caused
            reason = response.reason
            raise IntegrationAuthError(f"Brightree: {status_code} - {reason}")
        else:
            raise IntegrationAPIError(
                self.integration_name,
                f"Brightree: {status_code} - {response.headers}",
                status_code,
            )

    @staticmethod
    def _create_soup(r_text):
        return BeautifulSoup(r_text, "html.parser")

    @staticmethod
    def _extract_input_value_by_id(soup: BeautifulSoup, input_id: str) -> str:
        """
        Helper method to extract value from an input element by its ID
        """
        input_element = soup.find("input", {"id": input_id})
        return input_element.get("value") if input_element else None

    @staticmethod
    def _extract_input_value_by_name(soup: BeautifulSoup, input_name: str) -> str:
        """
        Helper method to extract value from an input element by its ID
        """
        input_element = soup.find("input", {"name": input_name})
        return input_element.get("value") if input_element else None

    @staticmethod
    def _create_form_data(form_data: dict):
        """
        Creates a URL-encoded form data string from a dictionary of key-value pairs.
        :param form_data: Dictionary containing form fields and their values.
        :return: URL-encoded form data string.
        """
        for key, value in form_data.items():
            try:
                if not isinstance(value, str):
                    value = json.dumps(value)
            except (TypeError, ValueError):
                pass
            finally:
                form_data[key] = value
        # URL-encode the dictionary into a query string
        return urllib.parse.urlencode(form_data, doseq=True)

    @staticmethod
    def _get_date_array(date_str: str = None) -> list[int]:
        if date_str:
            date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        else:
            date_obj = datetime.now()
        return [date_obj.year, date_obj.month, date_obj.day]

    @staticmethod
    def _format_date_mdy(date_str: str = None) -> str:
        if not date_str:
            return ""

        try:
            date_obj = datetime.strptime(date_str, '%Y-%m-%d')
            return date_obj.strftime('%-m/%-d/%Y')  # Linux/Mac
            # For Windows, use: return date_obj.strftime('%#m/%#d/%Y')
        except ValueError:
            return ""

    async def _get_create_patient_page(self, patient_key):
        url = f"{self.url}/F1/02873/Nation/Patient/frmPatientPersonal.aspx?PatientKey={patient_key}&Edit=1"
        response = await self._make_request(method="GET", url=url, headers=self.headers)
        return response

    async def create_update_patient(self, patient: BasePatient):
        if patient.patient_id == 0:
            patient_key = 0
        else:
            # retrieve patient info
            patient_key = await self._fetch_patient_key(patient_id=patient.patient_id)
            if patient_key is None:
                return {
                    "message": f"Unable to retrieve patient with ID: {patient.patient_id}",
                }

        url = f"{self.url}/F1/02873/Nation/Patient/frmPatientPersonal.aspx?PatientKey={patient_key}&Edit=1"
        page = await self._get_create_patient_page(patient_key=patient_key)
        soup = self._create_soup(page)

        view_state_val = self._extract_input_value_by_id(soup=soup, input_id="__VIEWSTATE")
        event_validation_val = self._extract_input_value_by_id(soup=soup, input_id="__EVENTVALIDATION")
        view_state_gen_val = self._extract_input_value_by_id(soup=soup, input_id="__VIEWSTATEGENERATOR")
        hf_lob_key = self._extract_input_value_by_name(
            soup, input_name="ctl00$ctl00$c$c$ucBillingAddressUpdate$hfLobKey"
        )
        cur_date_array = self._get_date_array()
        dob_input = self._format_date_mdy(patient.dob)

        form_data = {
            "ctl00$ctl00$pageSM": "ctl00$ctl00$ctl00$ctl00$c$btnContextSavePanel|ctl00$ctl00$c$btnContextSave",
            "__EVENTTARGET": "ctl00$ctl00$c$btnContextSave",
            "__EVENTARGUMENT": "",
            "__LASTFOCUS": "",
            "__VIEWSTATE": f"{view_state_val}",
            "__VIEWSTATEGENERATOR": f"{view_state_gen_val}",
            "__EVENTVALIDATION": f"{event_validation_val}",
            "ctl00_ctl00_c_EditContextMenu_ClientState": "",
            "ctl00_ctl00_c_SaveContextMenu_ClientState": "",
            "ctl00_ctl00_c_btnContextSave_ClientState": {
                "text": "",
                "value": "",
                "checked": False,
                "target": "",
                "navigateUrl": "",
                "commandName": "Save",
                "commandArgument": "",
                "autoPostBack": True,
                "selectedToggleStateIndex": 0,
                "validationGroup": None,
                "readOnly": False,
                "primary": False,
                "enabled": True
            },
            "ctl00_ctl00_c_btnSaveSplit_ClientState": {
                "text": "Save",
                "value": "",
                "checked": False,
                "target": "",
                "navigateUrl": "",
                "commandName": "",
                "commandArgument": "",
                "autoPostBack": False,
                "selectedToggleStateIndex": 0,
                "validationGroup": None,
                "readOnly": False,
                "primary": False,
                "enabled": True
            },
            "ctl00_ctl00_c_btnNewSalesOrder_ClientState": {
                "text": "New Sales Order",
                "value": "",
                "checked": False,
                "target": "",
                "navigateUrl": "",
                "commandName": "",
                "commandArgument": "",
                "autoPostBack": True,
                "selectedToggleStateIndex": 0,
                "validationGroup": None,
                "readOnly": False,
                "primary": False,
                "enabled": False
            },
            "ctl00_ctl00_c_btnPickup_ClientState": {
                "text": "New Pickup/Exchange",
                "value": "",
                "checked": False,
                "target": "",
                "navigateUrl": "",
                "commandName": "",
                "commandArgument": "",
                "autoPostBack": True,
                "selectedToggleStateIndex": 0,
                "validationGroup": None,
                "readOnly": False,
                "primary": False,
                "enabled": False
            },
            "ctl00_ctl00_c_btnLaunch_btnLaunch_Menu_ClientState": "",
            "ctl00_ctl00_c_btnLaunch_btnLaunch_Button_ClientState": {
                "text": "Launch",
                "value": "",
                "checked": False,
                "target": "",
                "navigateUrl": "",
                "commandName": "",
                "commandArgument": "",
                "autoPostBack": True,
                "selectedToggleStateIndex": 0,
                "validationGroup": None,
                "readOnly": False,
                "primary": False,
                "enabled": True
            },
            "ctl00_ctl00_c_PtAppRegControl_btnDoInvite_ClientState": {
                "text": "DoInvite",
                "value": "",
                "checked": False,
                "target": "",
                "navigateUrl": "",
                "commandName": "",
                "commandArgument": "",
                "autoPostBack": True,
                "selectedToggleStateIndex": 0,
                "validationGroup": None,
                "readOnly": False,
                "primary": False,
                "enabled": True
            },
            "ctl00_ctl00_c_PtAppRegControl_btnDoPasswordReset_ClientState": {
                "text": "DoPasswordReset",
                "value": "",
                "checked": False,
                "target": "",
                "navigateUrl": "",
                "commandName": "",
                "commandArgument": "",
                "autoPostBack": True,
                "selectedToggleStateIndex": 0,
                "validationGroup": None,
                "readOnly": False,
                "primary": False,
                "enabled": True
            },
            "ctl00_ctl00_c_PtAppRegControl_btnViewQRCode_ClientState": {
                "text": "DoInvite",
                "value": "",
                "checked": False,
                "target": "",
                "navigateUrl": "",
                "commandName": "",
                "commandArgument": "",
                "autoPostBack": True,
                "selectedToggleStateIndex": 0,
                "validationGroup": None,
                "readOnly": False,
                "primary": False,
                "enabled": True
            },
            "ctl00$ctl00$c$ssnControl$hfSSNRetrieved": "False",
            "ctl00$ctl00$c$ssnControl$hfSSN": "",
            "ctl00$ctl00$c$hdnShowBanner": "",
            "ctl00$ctl00$c$hdnDMEScriptShowBanner": "",
            "ctl00_ctl00_c_tsTop_ClientState": {
                "selectedIndexes": [
                    "1"
                ],
                "logEntries": [],
                "scrollState": {}
            },
            "ctl00$ctl00$c$c$txtLastName": f"{patient.name_last}",
            "ctl00$ctl00$c$c$txtFirstName": f"{patient.name_first}",
            "ctl00$ctl00$c$c$txtMiddleName": f"{patient.name_middle}",
            "ctl00$ctl00$c$c$txtPreferredName": f"{patient.name_preferred}",
            "ctl00$ctl00$c$c$txtSuffix": f"{patient.name_suffix}",
            "ctl00$ctl00$c$c$hmeDOB": f"{patient.dob}",
            "ctl00$ctl00$c$c$hmeDOB$dateInput": f"{dob_input}",
            "ctl00_ctl00_c_c_hmeDOB_dateInput_ClientState": {
                "enabled": True,
                "emptyMessage": "",
                "validationText": f"{patient.dob + '-00-00-00' if patient.dob != '' else ''}",
                "valueAsString": f"{patient.dob + '-00-00-00' if patient.dob != '' else ''}",
                "minDateStr": "1753-01-02-00-00-00",
                "maxDateStr": "9999-12-31-00-00-00",
                "lastSetTextBoxValue": dob_input,
            },
            "ctl00_ctl00_c_c_hmeDOB_calendar_SD": [],
            "ctl00_ctl00_c_c_hmeDOB_calendar_AD": [
                [1753, 1, 2], [9999, 12, 31], cur_date_array
            ],
            "ctl00_ctl00_c_c_hmeDOB_ClientState": {
                "minDateStr": "1753-01-02-00-00-00",
                "maxDateStr": "9999-12-31-00-00-00"
            },
            "ctl00$ctl00$c$c$ssnControl$hmeSSN": f"{patient.ssn}",
            "ctl00_ctl00_c_c_ssnControl_hmeSSN_ClientState": {
                "enabled": True,
                "emptyMessage": "",
                "validationText": "",
                "valueAsString": f"{patient.ssn}",
                "valueWithPromptAndLiterals": f"{patient.ssn}",
                "lastSetTextBoxValue": f"{patient.ssn}"
            },
            "ctl00$ctl00$c$c$ssnControl$currentSSNView$hfSSNRetrieved": "False",
            "ctl00$ctl00$c$c$ssnControl$currentSSNView$hfSSN": "",
            "ctl00$ctl00$c$c$ssnControl$tbSSNEdit$tb": "",
            "ctl00$ctl00$c$c$ssnControl$tbSSNConfirm$tb": "",
            "ctl00$ctl00$c$c$txtAccountNumber": "",
            "ctl00$ctl00$c$c$ddlCustomerType": "Patient",
            "ctl00$ctl00$c$c$txtPriorSystemKey": "",
            "ctl00$ctl00$c$c$MasterFacilityField$cbLookup_Input": [None],
            "ctl00$ctl00$c$c$MasterFacilityField$cbLookup_value": "0",
            "ctl00$ctl00$c$c$MasterFacilityField$cbLookup_text": [None],
            "ctl00$ctl00$c$c$MasterFacilityField$cbLookup_clientWidth": "150px",
            "ctl00$ctl00$c$c$MasterFacilityField$cbLookup_clientHeight": "14px",
            "ctl00_ctl00_c_c_btnCopyFacilityAddress_ClientState": {
                "text": "Copy Facility Address",
                "value": "",
                "checked": False,
                "target": "",
                "navigateUrl": "",
                "commandName": "",
                "commandArgument": "",
                "autoPostBack": True,
                "selectedToggleStateIndex": 0,
                "validationGroup": None,
                "readOnly": False,
                "primary": False,
                "enabled": False
            },
            "ctl00_ctl00_c_c_ucAlternateID_rdwManageAID_C_btnSaveAID_ClientState": {
                "text": "Save ",
                "value": "",
                "checked": False,
                "target": "",
                "navigateUrl": "",
                "commandName": "",
                "commandArgument": "",
                "autoPostBack": True,
                "selectedToggleStateIndex": 0,
                "validationGroup": None,
                "readOnly": False,
                "primary": False,
                "enabled": True
            },
            "ctl00_ctl00_c_c_ucAlternateID_rdwManageAID_C_btnDeleteAID_ClientState": {
                "text": "Delete",
                "value": "",
                "checked": False,
                "target": "",
                "navigateUrl": "",
                "commandName": "",
                "commandArgument": "",
                "autoPostBack": True,
                "selectedToggleStateIndex": 0,
                "validationGroup": None,
                "readOnly": False,
                "primary": False,
                "enabled": True
            },
            "ctl00_ctl00_c_c_ucAlternateID_rdwManageAID_C_btnCancelAID_ClientState": {
                "text": "Cancel",
                "value": "",
                "checked": False,
                "target": "",
                "navigateUrl": "",
                "commandName": "",
                "commandArgument": "",
                "autoPostBack": True,
                "selectedToggleStateIndex": 0,
                "validationGroup": None,
                "readOnly": False,
                "primary": False,
                "enabled": True
            },
            "ctl00_ctl00_c_c_ucAlternateID_rdwManageAID_ClientState": "",
            "ctl00_ctl00_c_c_ucAlternateID_rttAIDClear_ClientState": "",
            "ctl00_ctl00_c_c_btnCopyToInsured_ClientState": {
                "text": "Copy to Insured",
                "value": "",
                "checked": False,
                "target": "",
                "navigateUrl": "",
                "commandName": "",
                "commandArgument": "",
                "autoPostBack": True,
                "selectedToggleStateIndex": 0,
                "validationGroup": None,
                "readOnly": False,
                "primary": False,
                "enabled": True
            },
            "ctl00$ctl00$c$c$ucBillingAddressUpdate$hfLobKey": f"{hf_lob_key}",
            "ctl00_ctl00_c_c_ucBillingAddressUpdate_rdwManagePBA_C_btnValidateAddress_ClientState": {
                "text": "Validate",
                "value": "",
                "checked": False,
                "target": "",
                "navigateUrl": "",
                "commandName": "",
                "commandArgument": "",
                "autoPostBack": True,
                "selectedToggleStateIndex": 0,
                "validationGroup": None,
                "readOnly": False,
                "primary": False,
                "enabled": True
            },
            "ctl00_ctl00_c_c_ucBillingAddressUpdate_rdwManagePBA_C_btnCancelPBA_ClientState": {
                "text": "Cancel",
                "value": "",
                "checked": False,
                "target": "",
                "navigateUrl": "",
                "commandName": "",
                "commandArgument": "",
                "autoPostBack": True,
                "selectedToggleStateIndex": 0,
                "validationGroup": None,
                "readOnly": False,
                "primary": False,
                "enabled": True
            },
            "ctl00$ctl00$c$c$ucBillingAddressUpdate$rdwManagePBA$C$acAddressLine1": "",
            "ctl00$ctl00$c$c$ucBillingAddressUpdate$rdwManagePBA$C$AddressLine2Field": "",
            "ctl00$ctl00$c$c$ucBillingAddressUpdate$rdwManagePBA$C$AddressLine3Field": "",
            "ctl00$ctl00$c$c$ucBillingAddressUpdate$rdwManagePBA$C$CityField": "",
            "ctl00$ctl00$c$c$ucBillingAddressUpdate$rdwManagePBA$C$pbStateField": "CA",
            "ctl00$ctl00$c$c$ucBillingAddressUpdate$rdwManagePBA$C$pbCountyField": "0",
            "ctl00$ctl00$c$c$ucBillingAddressUpdate$rdwManagePBA$C$pbCountryField": "1",
            "ctl00$ctl00$c$c$ucBillingAddressUpdate$rdwManagePBA$C$pbPostalCodeField": "_____-____",
            "ctl00_ctl00_c_c_ucBillingAddressUpdate_rdwManagePBA_C_pbPostalCodeField_ClientState": {
                "enabled": True,
                "emptyMessage": "",
                "validationText": "",
                "valueAsString": "_____-____",
                "valueWithPromptAndLiterals": "_____-____",
                "lastSetTextBoxValue": "_____-____"
            },
            "ctl00_ctl00_c_c_ucBillingAddressUpdate_rdwManagePBA_ClientState": "",
            "ctl00_ctl00_c_c_ucBillingAddressUpdate_rttPBAClear_ClientState": "",
            "ctl00$ctl00$c$c$hmePhone": f"{patient.phone_home}",
            "ctl00_ctl00_c_c_hmePhone_ClientState": {
                "enabled": True,
                "emptyMessage": "",
                "validationText": f"{patient.phone_home if '_' not in patient.phone_home else ''}",
                "valueAsString": f"{patient.phone_home}",
                "valueWithPromptAndLiterals": f"{patient.phone_home}",
                "lastSetTextBoxValue": f"{patient.phone_home}"
            },
            "ctl00$ctl00$c$c$hmeFax": f"{patient.phone_fax}",
            "ctl00_ctl00_c_c_hmeFax_ClientState": {
                "enabled": True,
                "emptyMessage": "",
                "validationText": f"{patient.phone_fax if '_' not in patient.phone_fax else ''}",
                "valueAsString": f"{patient.phone_fax}",
                "valueWithPromptAndLiterals": f"{patient.phone_fax}",
                "lastSetTextBoxValue": f"{patient.phone_fax}"
            },
            "ctl00$ctl00$c$c$hmeMobilePhone": f"{patient.phone_mobile}",
            "ctl00_ctl00_c_c_hmeMobilePhone_ClientState": {
                "enabled": True,
                "emptyMessage": "",
                "validationText": f"{patient.phone_mobile if '_' not in patient.phone_mobile else ''}",
                "valueAsString": f"{patient.phone_mobile}",
                "valueWithPromptAndLiterals": f"{patient.phone_mobile}",
                "lastSetTextBoxValue": f"{patient.phone_mobile}"
            },
            "ctl00$ctl00$c$c$txtEmailAddress": f"{patient.email}",
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey66": "",
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey69": "",
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey73": "",
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey71": "",
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey23": "",
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey26": "",
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey31": "",
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey43": "",
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey32": "",
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey10": "",
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey20": "",
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey21": "",
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey30": "",
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey34": "",
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey27": "",
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey29": "",
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey46": "",
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey33": "",
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey49": "",
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey50": "",
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey64": "",
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey65": "",
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey70": "",
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey72": "",
            "ctl00$ctl00$c$c$CustomFields$hdnCustomFieldList": "",
            "ctl00$ctl00$c$c$txtDiscountPercent": "0%",
            "ctl00_ctl00_c_c_txtDiscountPercent_ClientState": {
                "enabled": False,
                "emptyMessage": "",
                "validationText": "0",
                "valueAsString": "0",
                "minValue": 0,
                "maxValue": 100,
                "lastSetTextBoxValue": "0%"
            },
            "ctl00$ctl00$c$c$luTaxZone$cbLookup_Input": [None],
            "ctl00$ctl00$c$c$luTaxZone$cbLookup_value": "0",
            "ctl00$ctl00$c$c$luTaxZone$cbLookup_text": [None],
            "ctl00$ctl00$c$c$luTaxZone$cbLookup_clientWidth": "150px",
            "ctl00$ctl00$c$c$luTaxZone$cbLookup_clientHeight": "14px",
            "ctl00$ctl00$c$c$ddlBranch": "102",
            "ctl00$ctl00$c$c$ddlAccountGroup": "0",
            "ctl00$ctl00$c$c$ddlPtGrp": "1",
            "ctl00$ctl00$c$c$txtUser1": "",
            "ctl00$ctl00$c$c$txtUser2": "",
            "ctl00$ctl00$c$c$txtUser3": "",
            "ctl00$ctl00$c$c$txtUser4": "",
            "ctl00$ctl00$c$c$ddlPOS": "4",
            "ctl00$ctl00$c$c$rdpDateOfAdmission": "",
            "ctl00$ctl00$c$c$rdpDateOfAdmission$dateInput": "",
            "ctl00_ctl00_c_c_rdpDateOfAdmission_dateInput_ClientState": {
                "enabled": True,
                "emptyMessage": "",
                "validationText": "",
                "valueAsString": "",
                "minDateStr": "1753-01-02-00-00-00",
                "maxDateStr": "9999-12-31-00-00-00",
                "lastSetTextBoxValue": ""
            },
            "ctl00_ctl00_c_c_rdpDateOfAdmission_calendar_SD": [],
            "ctl00_ctl00_c_c_rdpDateOfAdmission_calendar_AD": [
                [1753, 1, 2], [9999, 12, 31], cur_date_array
            ],
            "ctl00_ctl00_c_c_rdpDateOfAdmission_ClientState": {
                "minDateStr": "1753-01-02-00-00-00",
                "maxDateStr": "9999-12-31-00-00-00"
            },
            "ctl00$ctl00$c$c$rdpDateOfDischarge": "",
            "ctl00$ctl00$c$c$rdpDateOfDischarge$dateInput": "",
            "ctl00_ctl00_c_c_rdpDateOfDischarge_dateInput_ClientState": {
                "enabled": True,
                "emptyMessage": "",
                "validationText": "",
                "valueAsString": "",
                "minDateStr": "1753-01-02-00-00-00",
                "maxDateStr": "9999-12-31-00-00-00",
                "lastSetTextBoxValue": ""
            },
            "ctl00_ctl00_c_c_rdpDateOfDischarge_calendar_SD": [],
            "ctl00_ctl00_c_c_rdpDateOfDischarge_calendar_AD": [
                [1753, 1, 2], [9999, 12, 31], cur_date_array
            ],
            "ctl00_ctl00_c_c_rdpDateOfDischarge_ClientState": {
                "minDateStr": "1753-01-02-00-00-00",
                "maxDateStr": "9999-12-31-00-00-00"
            },
            "ctl00$ctl00$c$c$chkActiveAddress": "on",
            "ctl00_ctl00_c_c_btnAdditionDeliveryAddress_ClientState": {
                "text": "Additional Address",
                "value": "",
                "checked": False,
                "target": "",
                "navigateUrl": "",
                "commandName": "",
                "commandArgument": "",
                "autoPostBack": True,
                "selectedToggleStateIndex": 0,
                "validationGroup": None,
                "readOnly": False,
                "primary": False,
                "enabled": False
            },
            "ctl00$ctl00$c$c$ucAdditionalDeliveryAddress$hfLobKey": f"{hf_lob_key}",
            "ctl00_ctl00_c_c_ucAdditionalDeliveryAddress_rdwManagePBA_C_btnValidateAddress_ClientState": {
                "text": "Validate",
                "value": "",
                "checked": False,
                "target": "",
                "navigateUrl": "",
                "commandName": "",
                "commandArgument": "",
                "autoPostBack": True,
                "selectedToggleStateIndex": 0,
                "validationGroup": None,
                "readOnly": False,
                "primary": False,
                "enabled": True
            },
            "ctl00_ctl00_c_c_ucAdditionalDeliveryAddress_rdwManagePBA_C_btnCancelPBA_ClientState": {
                "text": "Cancel",
                "value": "",
                "checked": False,
                "target": "",
                "navigateUrl": "",
                "commandName": "",
                "commandArgument": "",
                "autoPostBack": True,
                "selectedToggleStateIndex": 0,
                "validationGroup": None,
                "readOnly": False,
                "primary": False,
                "enabled": True
            },
            "ctl00$ctl00$c$c$ucAdditionalDeliveryAddress$rdwManagePBA$C$acAddressLine1": "",
            "ctl00$ctl00$c$c$ucAdditionalDeliveryAddress$rdwManagePBA$C$AddressLine2Field": "",
            "ctl00$ctl00$c$c$ucAdditionalDeliveryAddress$rdwManagePBA$C$AddressLine3Field": "",
            "ctl00$ctl00$c$c$ucAdditionalDeliveryAddress$rdwManagePBA$C$CityField": "",
            "ctl00$ctl00$c$c$ucAdditionalDeliveryAddress$rdwManagePBA$C$pbStateField": "CA",
            "ctl00$ctl00$c$c$ucAdditionalDeliveryAddress$rdwManagePBA$C$pbCountyField": "0",
            "ctl00$ctl00$c$c$ucAdditionalDeliveryAddress$rdwManagePBA$C$pbCountryField": "1",
            "ctl00$ctl00$c$c$ucAdditionalDeliveryAddress$rdwManagePBA$C$pbPostalCodeField": "_____-____",
            "ctl00_ctl00_c_c_ucAdditionalDeliveryAddress_rdwManagePBA_C_pbPostalCodeField_ClientState": {
                "enabled": True,
                "emptyMessage": "",
                "validationText": "",
                "valueAsString": "_____-____",
                "valueWithPromptAndLiterals": "_____-____",
                "lastSetTextBoxValue": "_____-____"
            },
            "ctl00_ctl00_c_c_ucAdditionalDeliveryAddress_rdwManagePBA_ClientState": "",
            "ctl00_ctl00_c_c_ucAdditionalDeliveryAddress_rttPBAClear_ClientState": "",
            "ctl00$ctl00$c$c$PrimaryDeliveryAddress$i0$hfPtPrimaryDeliveryAddrKey": "",
            "ctl00_ctl00_c_c_PrimaryDeliveryAddress_i0_btnSameAsBillingAddressPrimary_ClientState": {
                "text": "Same as Billing Address",
                "value": "",
                "checked": False,
                "target": "",
                "navigateUrl": "",
                "commandName": "",
                "commandArgument": "",
                "autoPostBack": True,
                "selectedToggleStateIndex": 0,
                "validationGroup": None,
                "readOnly": False,
                "primary": False,
                "enabled": True
            },
            "ctl00$ctl00$c$c$PrimaryDeliveryAddress$i0$ucPrimaryAddressUpdate$hfLobKey": f"{hf_lob_key}",
            "ctl00_ctl00_c_c_PrimaryDeliveryAddress_i0_ucPrimaryAddressUpdate_rdwManagePBA_C_btnValidateAddress_ClientState": {
                "text": "Validate",
                "value": "",
                "checked": False,
                "target": "",
                "navigateUrl": "",
                "commandName": "",
                "commandArgument": "",
                "autoPostBack": True,
                "selectedToggleStateIndex": 0,
                "validationGroup": None,
                "readOnly": False,
                "primary": False,
                "enabled": True
            },
            "ctl00_ctl00_c_c_PrimaryDeliveryAddress_i0_ucPrimaryAddressUpdate_rdwManagePBA_C_btnCancelPBA_ClientState": {
                "text": "Cancel",
                "value": "",
                "checked": False,
                "target": "",
                "navigateUrl": "",
                "commandName": "",
                "commandArgument": "",
                "autoPostBack": True,
                "selectedToggleStateIndex": 0,
                "validationGroup": None,
                "readOnly": False,
                "primary": False,
                "enabled": True
            },
            "ctl00$ctl00$c$c$PrimaryDeliveryAddress$i0$ucPrimaryAddressUpdate$rdwManagePBA$C$acAddressLine1": "",
            "ctl00$ctl00$c$c$PrimaryDeliveryAddress$i0$ucPrimaryAddressUpdate$rdwManagePBA$C$AddressLine2Field": "",
            "ctl00$ctl00$c$c$PrimaryDeliveryAddress$i0$ucPrimaryAddressUpdate$rdwManagePBA$C$AddressLine3Field": "",
            "ctl00$ctl00$c$c$PrimaryDeliveryAddress$i0$ucPrimaryAddressUpdate$rdwManagePBA$C$CityField": "",
            "ctl00$ctl00$c$c$PrimaryDeliveryAddress$i0$ucPrimaryAddressUpdate$rdwManagePBA$C$pbStateField": "CA",
            "ctl00$ctl00$c$c$PrimaryDeliveryAddress$i0$ucPrimaryAddressUpdate$rdwManagePBA$C$pbCountyField": "0",
            "ctl00$ctl00$c$c$PrimaryDeliveryAddress$i0$ucPrimaryAddressUpdate$rdwManagePBA$C$pbCountryField": "1",
            "ctl00$ctl00$c$c$PrimaryDeliveryAddress$i0$ucPrimaryAddressUpdate$rdwManagePBA$C$pbPostalCodeField": "_____-____",
            "ctl00_ctl00_c_c_PrimaryDeliveryAddress_i0_ucPrimaryAddressUpdate_rdwManagePBA_C_pbPostalCodeField_ClientState": {
                "enabled": True,
                "emptyMessage": "",
                "validationText": "",
                "valueAsString": "_____-____",
                "valueWithPromptAndLiterals": "_____-____",
                "lastSetTextBoxValue": "_____-____"
            },
            "ctl00_ctl00_c_c_PrimaryDeliveryAddress_i0_ucPrimaryAddressUpdate_rdwManagePBA_ClientState": "",
            "ctl00_ctl00_c_c_PrimaryDeliveryAddress_i0_ucPrimaryAddressUpdate_rttPBAClear_ClientState": "",
            "ctl00$ctl00$c$c$PrimaryDeliveryAddress$i0$TxtDescription": "",
            "ctl00_ctl00_c_c_PrimaryDeliveryAddress_i0_TxtDescription_ClientState": {
                "enabled": True,
                "emptyMessage": "",
                "validationText": "",
                "valueAsString": "",
                "lastSetTextBoxValue": ""
            },
            "ctl00$ctl00$c$c$PrimaryDeliveryAddress$i0$hmeDelPrimaryPhone": "(___) ___-____",
            "ctl00_ctl00_c_c_PrimaryDeliveryAddress_i0_hmeDelPrimaryPhone_ClientState": {
                "enabled": True,
                "emptyMessage": "",
                "validationText": "",
                "valueAsString": "(___) ___-____",
                "valueWithPromptAndLiterals": "(___) ___-____",
                "lastSetTextBoxValue": "(___) ___-____"
            },
            "ctl00$ctl00$c$c$PrimaryDeliveryAddress$i0$ddlZonePrimary": "0",
            "ctl00_ctl00_c_c_PrimaryDeliveryAddress_ClientState": {
                "expandedItems": [
                    "0"
                ],
                "logEntries": [],
                "selectedItems": [
                    "0"
                ]
            },
            "ctl00_ctl00_c_c_rwUpdatePHEmail_ClientState": "",
            "ctl00_ctl00_c_c_rwOptInStatusPopup_ClientState": "",
            "ctl00_ctl00_c_c_rwAdditionalAddress_ClientState": "",
            "ctl00_ctl00_c_tsBot_ClientState": {
                "selectedIndexes": [
                    "1"
                ],
                "logEntries": [],
                "scrollState": {}
            },
            "radGridClickedRowIndex": "",
            "ptKey": "",
            "policyKey": "",
            "ctl00_ctl00_c_wctl00_ctl00_c_c_MasterFacilityField_cbLookup_ClientState": "",
            "ctl00_ctl00_c_wctl00_ctl00_c_c_luTaxZone_cbLookup_ClientState": "",
            "ctl00_ctl00_c_rwmPE_ClientState": "",
            "ctl00_ctl00_c_concurrencyWin_ClientState": "",
            "ctl00_ctl00_c_rwmMaster_ClientState": "",
            "ctl00_ctl00_c_rwmInvitePatient_ClientState": "",
            "ctl00_ctl00_c_GoScriptsRegisterWindow_ClientState": "",
            "ctl00_ctl00_c_DMEScriptsRegisterWindow_ClientState": "",
            "ctl00_ctl00_c_rwConnectWalkInPatient_ClientState": "",
            "ctl00_ctl00_c_rwAdhocMessage_ClientState": "",
            "ctl00_ctl00_c_rwTherapySearchForSO_ClientState": "",
            "ctl00$ctl00$c$ucMainFooter$isReadOnly": False,
            "__ASYNCPOST": True,
            "RadAJAXControlID": "ctl00_ctl00_pageRAM"
        }

        headers = self.headers.copy()
        headers["X-MicrosoftAjax"] = "Delta=True"
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=utf-8"

        data = self._create_form_data(form_data=form_data)
        create_response = await self._make_request(method="POST", url=url, headers=headers, data=data)

        redirect_page = create_response.split('||')[2].replace('|', '')
        redirect_page = unquote(redirect_page)
        if "exception" in redirect_page:
            raise IntegrationAPIError(
                integration_name="brightree",
                message=f"Failed to create/update new patient: {redirect_page}",
                status_code=500
            )

        redirect_url = f"{self.url}{redirect_page}"

        page_response = await self._make_request(method="GET", url=redirect_url, headers=headers)
        soup = self._create_soup(page_response)

        patient_id_label = soup.find('label', text='Patient ID')
        patient_id = (patient_id_label.parent.get_text(strip=True)
                      .replace('Patient ID', '').strip())
        patient_id = patient_id[:patient_id.find('DOB')]

        result = {
            "patient_id": patient_id,
            "patient_page": redirect_url,
        }
        return result

    async def search_patient(self, patient_id: str | int):
        patient_key = await self._fetch_patient_key(patient_id=patient_id)
        if patient_key is None:
            return {
                "message": f"Unable to find patient with ID: {patient_id}",
            }

        url = f"{self.url}/F1/02873/Nation/Patient/frmPatientPersonalRO.aspx"
        params = {
            'PatientKey': f'{patient_key}',
            'Edit': '0',
        }
        response = await self._make_request(method="GET", url=url, headers=self.headers, params=params)
        soup = self._create_soup(response)

        data_sections = soup.select('fieldset.longerLabel')
        patient_data = {}

        for section in data_sections:
            legend_elem = section.select_one("legend")
            legend_text = legend_elem.text.strip()

            list_items = section.select('li')
            list_dict = {}
            for list_item in list_items:
                li_label = list_item.select_one("label")
                li_label_text = li_label.text.strip()
                if li_label_text == '':
                    continue

                li_text = list_item.text.strip()
                li_text = li_text.replace(li_label_text, "")

                list_dict[li_label_text] = self._clean_string(li_text)

            patient_data[legend_text] = list_dict

        return patient_data

    async def _fetch_patient_key(self, patient_id: int | str):
        patient_id = str(patient_id)
        # get_url = f"{self.url}/F1/02873/Nation/OrderEntry/PatientSearch.aspx"
        # get_page = await self._make_request(method="GET", url=get_url, headers=self.headers)
        # soup = self._create_soup(get_page)
        #
        # view_state_val = self._extract_input_value_by_id(soup=soup, input_id="__VIEWSTATE")
        # event_validation_val = self._extract_input_value_by_id(soup=soup, input_id="__EVENTVALIDATION")
        # view_state_gen_val = self._extract_input_value_by_id(soup=soup, input_id="__VIEWSTATEGENERATOR")
        # cur_date_array = self._get_date_array()
        #
        # url = f"{self.url}/F1/02873/Nation/OrderEntry/PatientSearch.aspx"
        # data = {
        #     "m$ctl00$pageSM": "m$ctl00$m$ctl00$c$c$btnSearchPanel|m$ctl00$c$c$btnSearch",
        #     "__LASTFOCUS": "",
        #     "__EVENTTARGET": "m$ctl00$c$c$btnSearch",
        #     "__EVENTARGUMENT": "",
        #     "__VIEWSTATE": f"{view_state_val}",
        #     "__VIEWSTATEGENERATOR": f"{view_state_gen_val}",
        #     "__EVENTVALIDATION": f"{event_validation_val}",
        #     "m_ctl00_c_wm_ctl00_c_c_btlQuickLookup_cbLookup_ClientState": "",
        #     "m_ctl00_c_wm_ctl00_c_c_btlMasterFacilityField_cbLookup_ClientState": "",
        #     "m_ctl00_c_GeneralManager_ClientState": "",
        #     "m_ctl00_c_c_btnSearch_ClientState": {
        #         "text": "Search",
        #         "value": "",
        #         "checked": False,
        #         "target": "",
        #         "navigateUrl": "",
        #         "commandName": "",
        #         "commandArgument": "",
        #         "autoPostBack": True,
        #         "selectedToggleStateIndex": 0,
        #         "validationGroup": None,
        #         "readOnly": False,
        #         "primary": False,
        #         "enabled": True
        #     },
        #     "m_ctl00_c_c_btnNewPatient_ClientState": {
        #         "text": "New Patient",
        #         "value": "",
        #         "checked": False,
        #         "target": "",
        #         "navigateUrl": "",
        #         "commandName": "",
        #         "commandArgument": "",
        #         "autoPostBack": False,
        #         "selectedToggleStateIndex": 0,
        #         "validationGroup": None,
        #         "readOnly": False,
        #         "primary": False,
        #         "enabled": True
        #     },
        #     "m_ctl00_c_c_btnReset_ClientState": {
        #         "text": "Reset",
        #         "value": "",
        #         "checked": False,
        #         "target": "",
        #         "navigateUrl": "",
        #         "commandName": "",
        #         "commandArgument": "",
        #         "autoPostBack": False,
        #         "selectedToggleStateIndex": 0,
        #         "validationGroup": None,
        #         "readOnly": False,
        #         "primary": False,
        #         "enabled": True
        #     },
        #     "m$ctl00$c$c$btlQuickLookup$cbLookup_Input": "",
        #     "m$ctl00$c$c$btlQuickLookup$cbLookup_value": "",
        #     "m$ctl00$c$c$btlQuickLookup$cbLookup_text": "",
        #     "m$ctl00$c$c$btlQuickLookup$cbLookup_clientWidth": "170px",
        #     "m$ctl00$c$c$btlQuickLookup$cbLookup_clientHeight": "14px",
        #     "m_ctl00_c_c_rtsTopMenu_ClientState": {
        #         "selectedIndexes": [
        #             "0"
        #         ],
        #         "logEntries": [],
        #         "scrollState": {}
        #     },
        #     "m$ctl00$c$c$rtbPatientId": f"{patient_id}",
        #     "m_ctl00_c_c_rtbPatientId_ClientState": {
        #         "enabled": True,
        #         "emptyMessage": "",
        #         "validationText": f"{patient_id}",
        #         "valueAsString": f"{patient_id}",
        #         "valueWithPromptAndLiterals": f"{patient_id}",
        #         "lastSetTextBoxValue": f"{patient_id}"
        #     },
        #     "m$ctl00$c$c$rtbLastName": "",
        #     "m_ctl00_c_c_rtbLastName_ClientState": {
        #         "enabled": True,
        #         "emptyMessage": "",
        #         "validationText": "",
        #         "valueAsString": "",
        #         "lastSetTextBoxValue": ""
        #     },
        #     "m$ctl00$c$c$rtbFirstName": "",
        #     "m_ctl00_c_c_rtbFirstName_ClientState": {
        #         "enabled": True,
        #         "emptyMessage": "",
        #         "validationText": "",
        #         "valueAsString": "",
        #         "lastSetTextBoxValue": ""
        #     },
        #     "m$ctl00$c$c$rdpDOB": "",
        #     "m$ctl00$c$c$rdpDOB$dateInput": "",
        #     "m_ctl00_c_c_rdpDOB_dateInput_ClientState": {
        #         "enabled": True,
        #         "emptyMessage": "",
        #         "validationText": "",
        #         "valueAsString": "",
        #         "minDateStr": "1753-01-02-00-00-00",
        #         "maxDateStr": "9999-12-31-00-00-00",
        #         "lastSetTextBoxValue": ""
        #     },
        #     "m_ctl00_c_c_rdpDOB_calendar_SD": "[]",
        #     "m_ctl00_c_c_rdpDOB_calendar_AD": [
        #         [1753, 1, 2], [9999, 12, 31], cur_date_array
        #     ],
        #     "m_ctl00_c_c_rdpDOB_ClientState": {
        #         "minDateStr": "1753-01-02-00-00-00",
        #         "maxDateStr": "9999-12-31-00-00-00"
        #     },
        #     "m$ctl00$c$c$rmtbLast4SSN": "    ",
        #     "m_ctl00_c_c_rmtbLast4SSN_ClientState": {
        #         "enabled": True,
        #         "emptyMessage": "",
        #         "validationText": "",
        #         "valueAsString": "    ",
        #         "valueWithPromptAndLiterals": "    ",
        #         "lastSetTextBoxValue": "    "
        #     },
        #     "m$ctl00$c$c$rcbCustomerTypeField": "0",
        #     "m$ctl00$c$c$rtbPriorSystemKey": "",
        #     "m_ctl00_c_c_rtbPriorSystemKey_ClientState": {
        #         "enabled": True,
        #         "emptyMessage": "",
        #         "validationText": "",
        #         "valueAsString": "",
        #         "lastSetTextBoxValue": ""
        #     },
        #     "m$ctl00$c$c$btlMasterFacilityField$cbLookup_Input": "[All]",
        #     "m$ctl00$c$c$btlMasterFacilityField$cbLookup_value": "0",
        #     "m$ctl00$c$c$btlMasterFacilityField$cbLookup_text": "[All]",
        #     "m$ctl00$c$c$btlMasterFacilityField$cbLookup_clientWidth": "135px",
        #     "m$ctl00$c$c$btlMasterFacilityField$cbLookup_clientHeight": "14px",
        #     "m$ctl00$c$c$rmtbMobilePhone": "(___) ___-____",
        #     "m_ctl00_c_c_rmtbMobilePhone_ClientState": {
        #         "enabled": True,
        #         "emptyMessage": "",
        #         "validationText": "",
        #         "valueAsString": "(___) ___-____",
        #         "valueWithPromptAndLiterals": "(___) ___-____",
        #         "lastSetTextBoxValue": "(___) ___-____"
        #     },
        #     "m$ctl00$c$c$rmtbFax": "(___) ___-____",
        #     "m_ctl00_c_c_rmtbFax_ClientState": {
        #         "enabled": True,
        #         "emptyMessage": "",
        #         "validationText": "",
        #         "valueAsString": "(___) ___-____",
        #         "valueWithPromptAndLiterals": "(___) ___-____",
        #         "lastSetTextBoxValue": "(___) ___-____"
        #     },
        #     "m$ctl00$c$c$rtbEmailAddress": "",
        #     "m_ctl00_c_c_rtbEmailAddress_ClientState": {
        #         "enabled": True,
        #         "emptyMessage": "",
        #         "validationText": "",
        #         "valueAsString": "",
        #         "lastSetTextBoxValue": ""
        #     },
        #     "m$ctl00$c$c$rtbDeliveryAddress1": "",
        #     "m_ctl00_c_c_rtbDeliveryAddress1_ClientState": {
        #         "enabled": True,
        #         "emptyMessage": "",
        #         "validationText": "",
        #         "valueAsString": "",
        #         "lastSetTextBoxValue": ""
        #     },
        #     "m$ctl00$c$c$rtbDeliveryAddress2": "",
        #     "m_ctl00_c_c_rtbDeliveryAddress2_ClientState": {
        #         "enabled": True,
        #         "emptyMessage": "",
        #         "validationText": "",
        #         "valueAsString": "",
        #         "lastSetTextBoxValue": ""
        #     },
        #     "m$ctl00$c$c$rtbCity": "",
        #     "m_ctl00_c_c_rtbCity_ClientState": {
        #         "enabled": True,
        #         "emptyMessage": "",
        #         "validationText": "",
        #         "valueAsString": "",
        #         "lastSetTextBoxValue": ""
        #     },
        #     "m$ctl00$c$c$rcbState": "0",
        #     "m$ctl00$c$c$rcbCounty": "0",
        #     "m$ctl00$c$c$rmtbZipCode": "_____-____",
        #     "m_ctl00_c_c_rmtbZipCode_ClientState": {
        #         "enabled": True,
        #         "emptyMessage": "",
        #         "validationText": "",
        #         "valueAsString": "_____-____",
        #         "valueWithPromptAndLiterals": "_____-____",
        #         "lastSetTextBoxValue": "_____-____"
        #     },
        #     "m$ctl00$c$c$rmtbHomePhone": "(___) ___-____",
        #     "m_ctl00_c_c_rmtbHomePhone_ClientState": {
        #         "enabled": True,
        #         "emptyMessage": "",
        #         "validationText": "",
        #         "valueAsString": "(___) ___-____",
        #         "valueWithPromptAndLiterals": "(___) ___-____",
        #         "lastSetTextBoxValue": "(___) ___-____"
        #     },
        #     "m$ctl00$c$c$rcbBranch": "[All]",
        #     "m_ctl00_c_c_rcbBranch_ClientState": "",
        #     "m$ctl00$c$c$rtbAccountNumber": "",
        #     "m_ctl00_c_c_rtbAccountNumber_ClientState": {
        #         "enabled": True,
        #         "emptyMessage": "",
        #         "validationText": "",
        #         "valueAsString": "",
        #         "lastSetTextBoxValue": ""
        #     },
        #     "m$ctl00$c$c$rcbAccountGroup": "0",
        #     "m$ctl00$c$c$rcbPatientGroup": "0",
        #     "m$ctl00$c$c$rtbUser1": "",
        #     "m_ctl00_c_c_rtbUser1_ClientState": {
        #         "enabled": True,
        #         "emptyMessage": "",
        #         "validationText": "",
        #         "valueAsString": "",
        #         "lastSetTextBoxValue": ""
        #     },
        #     "m$ctl00$c$c$rtbUser2": "",
        #     "m_ctl00_c_c_rtbUser2_ClientState": {
        #         "enabled": True,
        #         "emptyMessage": "",
        #         "validationText": "",
        #         "valueAsString": "",
        #         "lastSetTextBoxValue": ""
        #     },
        #     "m$ctl00$c$c$rtbUser3": "",
        #     "m_ctl00_c_c_rtbUser3_ClientState": {
        #         "enabled": True,
        #         "emptyMessage": "",
        #         "validationText": "",
        #         "valueAsString": "",
        #         "lastSetTextBoxValue": ""
        #     },
        #     "m$ctl00$c$c$rtbUser4": "",
        #     "m_ctl00_c_c_rtbUser4_ClientState": {
        #         "enabled": True,
        #         "emptyMessage": "",
        #         "validationText": "",
        #         "valueAsString": "",
        #         "lastSetTextBoxValue": ""
        #     },
        #     "m$ctl00$c$c$rcbCMNAutoRenewal": "0",
        #     "m$ctl00$c$c$cfcPatient$CustomFieldKey66": "",
        #     "m$ctl00$c$c$cfcPatient$CustomFieldKey73": "",
        #     "m$ctl00$c$c$cfcPatient$CustomFieldKey23": "",
        #     "m$ctl00$c$c$cfcPatient$CustomFieldKey31": "",
        #     "m$ctl00$c$c$cfcPatient$CustomFieldKey32": "",
        #     "m$ctl00$c$c$cfcPatient$CustomFieldKey20": "",
        #     "m$ctl00$c$c$cfcPatient$CustomFieldKey30": "",
        #     "m$ctl00$c$c$cfcPatient$CustomFieldKey27": "",
        #     "m$ctl00$c$c$cfcPatient$CustomFieldKey46": "",
        #     "m$ctl00$c$c$cfcPatient$CustomFieldKey49": "",
        #     "m$ctl00$c$c$cfcPatient$CustomFieldKey64": "",
        #     "m$ctl00$c$c$cfcPatient$CustomFieldKey70": "",
        #     "m$ctl00$c$c$cfcPatient$CustomFieldKey69": "",
        #     "m$ctl00$c$c$cfcPatient$CustomFieldKey71": "",
        #     "m$ctl00$c$c$cfcPatient$CustomFieldKey26": "",
        #     "m$ctl00$c$c$cfcPatient$CustomFieldKey43": "",
        #     "m$ctl00$c$c$cfcPatient$CustomFieldKey10": "",
        #     "m$ctl00$c$c$cfcPatient$CustomFieldKey21": "",
        #     "m$ctl00$c$c$cfcPatient$CustomFieldKey34": "",
        #     "m$ctl00$c$c$cfcPatient$CustomFieldKey29": "",
        #     "m$ctl00$c$c$cfcPatient$CustomFieldKey33": "",
        #     "m$ctl00$c$c$cfcPatient$CustomFieldKey50": "",
        #     "m$ctl00$c$c$cfcPatient$CustomFieldKey65": "",
        #     "m$ctl00$c$c$cfcPatient$CustomFieldKey72": "",
        #     "m$ctl00$c$c$cfcPatient$hdnCustomFieldList": "",
        #     "m$ctl00$c$c$rgResults$ctl00$ctl03$ctl01$PageSizeComboBox": "20",
        #     "m_ctl00_c_c_rgResults_ctl00_ctl03_ctl01_PageSizeComboBox_ClientState": "",
        #     "m_ctl00_c_c_rgResults_ClientState": "",
        #     "m_ctl00_c_c_rmpPatientSearch_ClientState": "",
        #     "m_ctl00_c_c_rwConnectWalkInPatient_ClientState": "",
        #     "m_ctl00_c_rwEditUserCredentials_ClientState": "",
        #     "m_ctl00_c_pageRWM_ClientState": "",
        #     "m$ctl00$c$ucMainFooter$isReadOnly": "True",
        #     "__ASYNCPOST": "True",
        #     "RadAJAXControlID": "m_ctl00_pageRAM"
        # }
        # headers = self.headers.copy()
        # headers["X-MicrosoftAjax"] = "Delta=true"
        # headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        # search_response = await self._make_request(url=url, method="POST", data=self._create_form_data(data), headers=headers)
        #
        # search_soup = self._create_soup(search_response)
        # result_row = search_soup.select_one("tr[id^='m_ctl00_c_c_rgResults_ctl00_']")
        # if result_row is not None:
        #     result_anchor = result_row.select_one("a[title='View Patient']")
        #     if result_anchor:
        #         result_url = result_anchor.get("href")
        #         match = re.search(r'PatientPopup\((\d+)', result_url)
        #         if match:
        #             patient_key = match.group(1)
        #             return patient_key
        #
        # return None

        timestamp = int(time.time() * 1000)
        params = {
            'rcbID': 'm_ctl00_c_c_luPatients_cbLookup',
            'rcbServerID': 'cbLookup',
            'text': f'{patient_id}',
            'comboText': f'{patient_id}',
            'comboValue': '',
            'skin': 'WindowsXP',
            'clientDataString': '[]',
            'timeStamp': f'{timestamp}',
        }
        url = f"{self.url}/F1/02873/Nation/OrderEntry/frmSalesOrderSearch.aspx"
        headers = self.headers.copy()
        headers["Content-Type"] = "application/json; charset=utf-8"

        response = await self._make_request(method="GET", url=url, headers=headers, params=params)
        response = json.loads(response)
        for item in response.get('Items', []):
            if 'Attributes' in item and item['Attributes'].get('PtID') == patient_id:
                return item.get('Value')

        return None

    async def create_sales_order(self, order: SalesOrder):
        patient_key = await self._fetch_patient_key(patient_id=order.patient_id)
        url = f"{self.url}/F1/02873/Nation/OrderEntry/frmSOOrder.aspx"
        params = {
            'Edit': '1',
            'SalesOrderKey': '0',
            'SalesOrderTypeKey': '1',
            'PatientKey': f'{patient_key}',
        }
        page_response = await self._make_request(url=url, method="GET", data=params, headers=self.headers)
        page_soup = self._create_soup(page_response)
        view_state = self._extract_input_value_by_id(page_soup, "__VIEWSTATE")
        view_state_generator = self._extract_input_value_by_id(page_soup, "__VIEWSTATEGENERATOR")
        event_validation = self._extract_input_value_by_id(page_soup, "__EVENTVALIDATION")
        hf_lob_key = self._extract_input_value_by_id(page_soup, "ctl00$ctl00$c$c$DeliveryAddrVerification$hfLobKey")

        actual_date_mdy = self._format_date_mdy(order.actual_date)
        scheduled_date_mdy = self._format_date_mdy(order.scheduled_date)

        actual_datetime = self._combine_datetime(order.actual_date, order.actual_time)
        scheduled_datetime = self._combine_datetime(order.scheduled_date, order.scheduled_time)

        cur_date_array = self._get_date_array()

        data = {
            "ctl00$ctl00$pageSM": "ctl00$ctl00$ctl00$ctl00$c$pnlSOData1Panel|ctl00$ctl00$c$btnContextSave",
            "PageLoadedHiddenTxtBox": "Set",
            "__LASTFOCUS": "",
            "__EVENTTARGET": "ctl00$ctl00$c$btnContextSave",
            "__EVENTARGUMENT": "",
            "__VIEWSTATE": f"{view_state}",
            "__VIEWSTATEGENERATOR": f"{view_state_generator}",
            "__EVENTVALIDATION": f"{event_validation}",
            "ctl00$ctl00$c$hdnRefresh": "",
            "radGridClickedRowIndex": "",
            "PuExKey": "",
            "policyKey": "",
            "ctl00_ctl00_c_EditContextMenu_ClientState": "",
            "ctl00_ctl00_c_SaveContextMenu_ClientState": "",
            "ctl00_ctl00_c_btnContextSave_ClientState": {
                "text": "",
                "value": "",
                "checked": False,
                "target": "",
                "navigateUrl": "",
                "commandName": "Save",
                "commandArgument": "",
                "autoPostBack": True,
                "selectedToggleStateIndex": 0,
                "validationGroup": None,
                "readOnly": False,
                "primary": False,
                "enabled": True
            },
            "ctl00_ctl00_c_btnSaveSplit_ClientState": {
                "text": "Save",
                "value": "",
                "checked": False,
                "target": "",
                "navigateUrl": "",
                "commandName": "",
                "commandArgument": "",
                "autoPostBack": False,
                "selectedToggleStateIndex": 0,
                "validationGroup": None,
                "readOnly": False,
                "primary": False,
                "enabled": True
            },
            "ctl00_ctl00_c_btnConfirm_ClientState": {
                "text": "Confirm",
                "value": "",
                "checked": False,
                "target": "",
                "navigateUrl": "",
                "commandName": "",
                "commandArgument": "",
                "autoPostBack": True,
                "selectedToggleStateIndex": 0,
                "validationGroup": None,
                "readOnly": False,
                "primary": False,
                "enabled": False
            },
            "ctl00_ctl00_c_btnPrint_btnPrint_Menu_ClientState": "",
            "ctl00_ctl00_c_btnPrint_btnPrint_Button_ClientState": {
                "text": "Print",
                "value": "",
                "checked": False,
                "target": "",
                "navigateUrl": "",
                "commandName": "Print",
                "commandArgument": "",
                "autoPostBack": True,
                "selectedToggleStateIndex": 0,
                "validationGroup": None,
                "readOnly": False,
                "primary": False,
                "enabled": False
            },
            "ctl00_ctl00_c_btnSendPOD_ClientState": {
                "text": "Send POD",
                "value": "",
                "checked": False,
                "target": "",
                "navigateUrl": "",
                "commandName": "",
                "commandArgument": "",
                "autoPostBack": True,
                "selectedToggleStateIndex": 0,
                "validationGroup": None,
                "readOnly": False,
                "primary": False,
                "enabled": False
            },
            "ctl00_ctl00_c_btnEligibility_ClientState": {
                "text": "Eligibility",
                "value": "",
                "checked": False,
                "target": "",
                "navigateUrl": "",
                "commandName": "",
                "commandArgument": "",
                "autoPostBack": True,
                "selectedToggleStateIndex": 0,
                "validationGroup": None,
                "readOnly": False,
                "primary": False,
                "enabled": True
            },
            "ctl00_ctl00_c_btnVoidNew_btnVoidNew_Menu_ClientState": "",
            "ctl00_ctl00_c_btnVoidNew_btnVoidNew_Button_ClientState": {
                "text": "Void",
                "value": "",
                "checked": False,
                "target": "",
                "navigateUrl": "",
                "commandName": "",
                "commandArgument": "",
                "autoPostBack": False,
                "selectedToggleStateIndex": 0,
                "validationGroup": None,
                "readOnly": False,
                "primary": False,
                "enabled": True
            },
            "ctl00_ctl00_c_hypNote_ClientState": {
                "text": "New Patient Note",
                "value": "",
                "checked": False,
                "target": "",
                "navigateUrl": "",
                "commandName": "",
                "commandArgument": "",
                "autoPostBack": True,
                "selectedToggleStateIndex": 0,
                "validationGroup": None,
                "readOnly": False,
                "primary": False,
                "enabled": False
            },
            "ctl00_ctl00_c_btnPostBack_ClientState": {
                "text": "",
                "value": "",
                "checked": False,
                "target": "",
                "navigateUrl": "",
                "commandName": "",
                "commandArgument": "",
                "autoPostBack": True,
                "selectedToggleStateIndex": 0,
                "validationGroup": None,
                "readOnly": False,
                "primary": False,
                "enabled": True
            },
            "ctl00$ctl00$c$ssnControl$hfSSNRetrieved": False,
            "ctl00$ctl00$c$ssnControl$hfSSN": "",
            "ctl00_ctl00_c_PtAppRegControl_btnDoInvite_ClientState": {
                "text": "DoInvite",
                "value": "",
                "checked": False,
                "target": "",
                "navigateUrl": "",
                "commandName": "",
                "commandArgument": "",
                "autoPostBack": True,
                "selectedToggleStateIndex": 0,
                "validationGroup": None,
                "readOnly": False,
                "primary": False,
                "enabled": True
            },
            "ctl00_ctl00_c_PtAppRegControl_btnDoPasswordReset_ClientState": {
                "text": "DoPasswordReset",
                "value": "",
                "checked": False,
                "target": "",
                "navigateUrl": "",
                "commandName": "",
                "commandArgument": "",
                "autoPostBack": True,
                "selectedToggleStateIndex": 0,
                "validationGroup": None,
                "readOnly": False,
                "primary": False,
                "enabled": True
            },
            "ctl00_ctl00_c_PtAppRegControl_btnViewQRCode_ClientState": {
                "text": "DoInvite",
                "value": "",
                "checked": False,
                "target": "",
                "navigateUrl": "",
                "commandName": "",
                "commandArgument": "",
                "autoPostBack": True,
                "selectedToggleStateIndex": 0,
                "validationGroup": None,
                "readOnly": False,
                "primary": False,
                "enabled": True
            },
            "ctl00_ctl00_c_tsTop_ClientState": {
                "selectedIndexes": [
                    "0"
                ],
                "logEntries": [],
                "scrollState": {}
            },
            "ctl00$ctl00$c$c$rdpScheduledDeliveryDate": f"{order.scheduled_date}",
            "ctl00$ctl00$c$c$rdpScheduledDeliveryDate$dateInput": f"{scheduled_date_mdy}",
            "ctl00_ctl00_c_c_rdpScheduledDeliveryDate_dateInput_ClientState": {
                "enabled": True,
                "emptyMessage": "",
                "validationText": f"{order.scheduled_date}-00-00-00" if order.scheduled_date != "" else "",
                "valueAsString": f"{order.scheduled_date}-00-00-00" if order.scheduled_date != "" else "",
                "minDateStr": "1753-01-02-00-00-00",
                "maxDateStr": "9999-12-31-00-00-00",
                "lastSetTextBoxValue": f"{scheduled_date_mdy}"
            },
            "ctl00_ctl00_c_c_rdpScheduledDeliveryDate_ClientState": {
                "minDateStr": "1753-01-02-00-00-00",
                "maxDateStr": "9999-12-31-00-00-00"
            },
            "ctl00$ctl00$c$c$rtpScheduledDeliveryTime": f"{scheduled_datetime}",
            "ctl00$ctl00$c$c$rtpScheduledDeliveryTime$dateInput": f"{order.scheduled_time}",
            "ctl00_ctl00_c_c_rtpScheduledDeliveryTime_dateInput_ClientState": {
                "enabled": True,
                "emptyMessage": "",
                "validationText": f"{scheduled_datetime}",
                "valueAsString": f"{scheduled_datetime}",
                "minDateStr": "1980-01-01-00-00-00",
                "maxDateStr": "2099-12-31-00-00-00",
                "lastSetTextBoxValue": f"{order.scheduled_time}"
            },
            "ctl00_ctl00_c_c_rtpScheduledDeliveryTime_timeView_ClientState": "",
            "ctl00_ctl00_c_c_rtpScheduledDeliveryTime_ClientState": "",
            "ctl00$ctl00$c$c$rdpActualDeliveryDate": f"{order.actual_date}",
            "ctl00$ctl00$c$c$rdpActualDeliveryDate$dateInput": f"{actual_date_mdy}",
            "ctl00_ctl00_c_c_rdpActualDeliveryDate_dateInput_ClientState": {
                "enabled": True,
                "emptyMessage": "",
                "validationText": f"{order.actual_date}-00-00-00" if order.actual_date != "" else "",
                "valueAsString": f"{order.actual_date}-00-00-00" if order.actual_date != "" else "",
                "minDateStr": "1753-01-02-00-00-00",
                "maxDateStr": "9999-12-31-00-00-00",
                "lastSetTextBoxValue": f"{actual_date_mdy}"
            },
            "ctl00_ctl00_c_c_rdpActualDeliveryDate_ClientState": {
                "minDateStr": "1753-01-02-00-00-00",
                "maxDateStr": "9999-12-31-00-00-00"
            },
            "ctl00$ctl00$c$c$rtpActualDeliveryTime": f"{actual_datetime}",
            "ctl00$ctl00$c$c$rtpActualDeliveryTime$dateInput": f"{order.actual_time}",
            "ctl00_ctl00_c_c_rtpActualDeliveryTime_dateInput_ClientState": {
                "enabled": True,
                "emptyMessage": "",
                "validationText": f"{actual_datetime}",
                "valueAsString": f"{actual_datetime}",
                "minDateStr": "1980-01-01-00-00-00",
                "maxDateStr": "2099-12-31-00-00-00",
                "lastSetTextBoxValue": f"{order.actual_time}"
            },
            "ctl00_ctl00_c_c_rtpActualDeliveryTime_timeView_ClientState": "",
            "ctl00_ctl00_c_c_rtpActualDeliveryTime_ClientState": "",
            "ctl00$ctl00$c$c$DeliveryAddrVerification$hfLobKey": f"{hf_lob_key}",
            "ctl00_ctl00_c_c_DeliveryAddrVerification_rdwManagePBA_C_btnValidateAddress_ClientState": {
                "text": "Validate",
                "value": "",
                "checked": False,
                "target": "",
                "navigateUrl": "",
                "commandName": "",
                "commandArgument": "",
                "autoPostBack": True,
                "selectedToggleStateIndex": 0,
                "validationGroup": None,
                "readOnly": False,
                "primary": False,
                "enabled": True
            },
            "ctl00_ctl00_c_c_DeliveryAddrVerification_rdwManagePBA_C_btnCancelPBA_ClientState": {
                "text": "Cancel",
                "value": "",
                "checked": False,
                "target": "",
                "navigateUrl": "",
                "commandName": "",
                "commandArgument": "",
                "autoPostBack": True,
                "selectedToggleStateIndex": 0,
                "validationGroup": None,
                "readOnly": False,
                "primary": False,
                "enabled": True
            },
            "ctl00$ctl00$c$c$DeliveryAddrVerification$rdwManagePBA$C$acAddressLine1": "",
            "ctl00$ctl00$c$c$DeliveryAddrVerification$rdwManagePBA$C$AddressLine2Field": "",
            "ctl00$ctl00$c$c$DeliveryAddrVerification$rdwManagePBA$C$AddressLine3Field": "",
            "ctl00$ctl00$c$c$DeliveryAddrVerification$rdwManagePBA$C$CityField": "",
            "ctl00$ctl00$c$c$DeliveryAddrVerification$rdwManagePBA$C$pbStateField": "CA",
            "ctl00$ctl00$c$c$DeliveryAddrVerification$rdwManagePBA$C$pbCountyField": "0",
            "ctl00$ctl00$c$c$DeliveryAddrVerification$rdwManagePBA$C$pbCountryField": "1",
            "ctl00$ctl00$c$c$DeliveryAddrVerification$rdwManagePBA$C$pbPostalCodeField": "_____-____",
            "ctl00_ctl00_c_c_DeliveryAddrVerification_rdwManagePBA_C_pbPostalCodeField_ClientState": {
                "enabled": True,
                "emptyMessage": "",
                "validationText": "",
                "valueAsString": "_____-____",
                "valueWithPromptAndLiterals": "_____-____",
                "lastSetTextBoxValue": "_____-____"
            },
            "ctl00_ctl00_c_c_DeliveryAddrVerification_rdwManagePBA_ClientState": "",
            "ctl00_ctl00_c_c_DeliveryAddrVerification_rttPBAClear_ClientState": "",
            "ctl00$ctl00$c$c$hmeDeliveryPhone": f"{order.phone_delivery}",
            "ctl00_ctl00_c_c_hmeDeliveryPhone_ClientState": {
                "enabled": True,
                "emptyMessage": "",
                "validationText": f"{order.phone_delivery if '_' not in order.phone_delivery else ''}",
                "valueAsString": f"{order.phone_delivery}",
                "valueWithPromptAndLiterals": f"{order.phone_delivery}",
                "lastSetTextBoxValue": f"{order.phone_delivery}",
            },
            "ctl00$ctl00$c$c$hmeDeliveryMobile": f"{order.phone_mobile}",
            "ctl00_ctl00_c_c_hmeDeliveryMobile_ClientState": {
                "enabled": True,
                "emptyMessage": "",
                "validationText": f"{order.phone_mobile if '_' not in order.phone_mobile else ''}",
                "valueAsString": f"{order.phone_mobile}",
                "valueWithPromptAndLiterals": f"{order.phone_mobile}",
                "lastSetTextBoxValue": f"{order.phone_mobile}",
            },
            "ctl00$ctl00$c$c$MasterFacilityField$cbLookup_Input": [None],
            "ctl00$ctl00$c$c$MasterFacilityField$cbLookup_value": "0",
            "ctl00$ctl00$c$c$MasterFacilityField$cbLookup_text": [None],
            "ctl00$ctl00$c$c$MasterFacilityField$cbLookup_clientWidth": "195px",
            "ctl00$ctl00$c$c$MasterFacilityField$cbLookup_clientHeight": "14px",
            "ctl00$ctl00$c$c$luTaxZone$cbLookup_Input": "Corporate",
            "ctl00$ctl00$c$c$luTaxZone$cbLookup_value": "2",
            "ctl00$ctl00$c$c$luTaxZone$cbLookup_text": "Corporate",
            "ctl00$ctl00$c$c$luTaxZone$cbLookup_clientWidth": "195px",
            "ctl00$ctl00$c$c$luTaxZone$cbLookup_clientHeight": "14px",
            "ctl00$ctl00$c$c$txtNote": f"{order.order_notes}",
            "ctl00$ctl00$c$c$txtDeliveryNote": f"{order.delivery_notes}",
            "ctl00$ctl00$c$c$ddlSetupMethod": "0",
            "ctl00$ctl00$c$c$luDeliveryTechnician$cbLookup_Input": [None],
            "ctl00$ctl00$c$c$luDeliveryTechnician$cbLookup_value": "0",
            "ctl00$ctl00$c$c$luDeliveryTechnician$cbLookup_text": [None],
            "ctl00$ctl00$c$c$luDeliveryTechnician$cbLookup_clientWidth": "135px",
            "ctl00$ctl00$c$c$luDeliveryTechnician$cbLookup_clientHeight": "14px",
            "ctl00$ctl00$c$c$ddlFulfillmentVendor": "0",
            "ctl00_ctl00_c_c_ucPODStatus_btnCancelPOD_ClientState": {
                "text": "Cancel POD",
                "value": "",
                "checked": False,
                "target": "",
                "navigateUrl": "",
                "commandName": "",
                "commandArgument": "",
                "autoPostBack": True,
                "selectedToggleStateIndex": 0,
                "validationGroup": None,
                "readOnly": False,
                "primary": False,
                "enabled": True
            },
            "ctl00$ctl00$c$c$BTddlShipCarrier": "0",
            "ctl00$ctl00$c$c$BTddlShipMethod": "0",
            "ctl00_ctl00_c_c_btnSendBrightShip_ClientState": {
                "text": "Send to BrightSHIP",
                "value": "",
                "checked": False,
                "target": "",
                "navigateUrl": "",
                "commandName": "",
                "commandArgument": "",
                "autoPostBack": True,
                "selectedToggleStateIndex": 0,
                "validationGroup": None,
                "readOnly": False,
                "primary": False,
                "enabled": False
            },
            "ctl00$ctl00$c$c$BTddlTrackCarrier": "0",
            "ctl00$ctl00$c$c$txtnewTracking": "",
            "ctl00_ctl00_c_c_BTShipTrackingAdd_ClientState": {
                "text": "Add Tracking",
                "value": "",
                "checked": False,
                "target": "",
                "navigateUrl": "",
                "commandName": "",
                "commandArgument": "",
                "autoPostBack": True,
                "selectedToggleStateIndex": 0,
                "validationGroup": None,
                "readOnly": False,
                "primary": False,
                "enabled": False
            },
            "ctl00$ctl00$c$c$BTShipTracking$ctl00$ctl03$ctl01$PageSizeComboBox": "5",
            "ctl00_ctl00_c_c_BTShipTracking_ctl00_ctl03_ctl01_PageSizeComboBox_ClientState": "",
            "ctl00_ctl00_c_c_BTShipTracking_ClientState": "",
            "ctl00$ctl00$c$c$ddlManualHoldReason": "0",
            "ctl00$ctl00$c$c$rdpStopDate": "",
            "ctl00$ctl00$c$c$rdpStopDate$dateInput": "",
            "ctl00_ctl00_c_c_rdpStopDate_dateInput_ClientState": {
                "enabled": True,
                "emptyMessage": "",
                "validationText": "",
                "valueAsString": "",
                "minDateStr": "1753-01-02-00-00-00",
                "maxDateStr": "9999-12-31-00-00-00",
                "lastSetTextBoxValue": ""
            },
            "ctl00_ctl00_c_c_rdpStopDate_ClientState": {
                "minDateStr": "1753-01-02-00-00-00",
                "maxDateStr": "9999-12-31-00-00-00"
            },
            "ctl00$ctl00$c$c$ddlStopReason": "0",
            "ctl00$ctl00$c$c$ddlBranch": "102",
            "ctl00$ctl00$c$c$luLocation$cbLookup_Input": "AA - Nationwide Medical, Inc.",
            "ctl00$ctl00$c$c$luLocation$cbLookup_value": "102",
            "ctl00$ctl00$c$c$luLocation$cbLookup_text": "AA - Nationwide Medical, Inc.",
            "ctl00$ctl00$c$c$luLocation$cbLookup_clientWidth": "135px",
            "ctl00$ctl00$c$c$luLocation$cbLookup_clientHeight": "14px",
            "ctl00$ctl00$c$c$ddlStatus": "1",
            "ctl00$ctl00$c$c$ddlClassification": "0",
            "ctl00$ctl00$c$c$ddlPlaceOfService": "4",
            "ctl00$ctl00$c$c$rdpDateOfAdmission": "",
            "ctl00$ctl00$c$c$rdpDateOfAdmission$dateInput": "",
            "ctl00_ctl00_c_c_rdpDateOfAdmission_dateInput_ClientState": {
                "enabled": True,
                "emptyMessage": "",
                "validationText": "",
                "valueAsString": "",
                "minDateStr": "1753-01-02-00-00-00",
                "maxDateStr": "9999-12-31-00-00-00",
                "lastSetTextBoxValue": ""
            },
            "ctl00_ctl00_c_c_rdpDateOfAdmission_ClientState": {
                "minDateStr": "1753-01-02-00-00-00",
                "maxDateStr": "9999-12-31-00-00-00"
            },
            "ctl00$ctl00$c$c$rdpDateOfDischarge": "",
            "ctl00$ctl00$c$c$rdpDateOfDischarge$dateInput": "",
            "ctl00_ctl00_c_c_rdpDateOfDischarge_dateInput_ClientState": {
                "enabled": True,
                "emptyMessage": "",
                "validationText": "",
                "valueAsString": "",
                "minDateStr": "1753-01-02-00-00-00",
                "maxDateStr": "9999-12-31-00-00-00",
                "lastSetTextBoxValue": ""
            },
            "ctl00_ctl00_c_c_rdpDateOfDischarge_ClientState": {
                "minDateStr": "1753-01-02-00-00-00",
                "maxDateStr": "9999-12-31-00-00-00"
            },
            "ctl00$ctl00$c$c$txtDiscountPercent": "0%",
            "ctl00_ctl00_c_c_txtDiscountPercent_ClientState": {
                "enabled": False,
                "emptyMessage": "",
                "validationText": "0",
                "valueAsString": "0",
                "minValue": 0,
                "maxValue": 100,
                "lastSetTextBoxValue": "0%"
            },
            "ctl00$ctl00$c$c$txtPO": "",
            "ctl00$ctl00$c$c$txtRefNum": "",
            "ctl00$ctl00$c$c$tbUser1": "",
            "ctl00$ctl00$c$c$tbUser2": "",
            "ctl00$ctl00$c$c$tbUser3": "",
            "ctl00$ctl00$c$c$tbUser4": "",
            "ctl00$ctl00$c$c$tbPriorSystemKey": "",
            "ctl00$ctl00$c$c$ddlWIPState": "0",
            "ctl00$ctl00$c$c$luAssignedTo$cbLookup_Input": "[All]",
            "ctl00$ctl00$c$c$luAssignedTo$cbLookup_value": "0",
            "ctl00$ctl00$c$c$luAssignedTo$cbLookup_text": "[All]",
            "ctl00$ctl00$c$c$luAssignedTo$cbLookup_clientWidth": "135px",
            "ctl00$ctl00$c$c$luAssignedTo$cbLookup_clientHeight": "14px",
            "ctl00$ctl00$c$c$rdpDateNeededStd": "",
            "ctl00$ctl00$c$c$rdpDateNeededStd$dateInput": "",
            "ctl00_ctl00_c_c_rdpDateNeededStd_dateInput_ClientState": {
                "enabled": False,
                "emptyMessage": "",
                "validationText": "",
                "valueAsString": "",
                "minDateStr": "1753-01-02-00-00-00",
                "maxDateStr": "9999-12-31-00-00-00",
                "lastSetTextBoxValue": ""
            },
            "ctl00_ctl00_c_c_rdpDateNeededStd_ClientState": {
                "minDateStr": "1753-01-02-00-00-00",
                "maxDateStr": "9999-12-31-00-00-00"
            },
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey67": "",
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey11": "",
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey12": "",
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey62": "",
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey13": "",
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey63": "",
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey41": "",
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey28": "",
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey18": "",
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey24": "",
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey24$dateInput": "",
            "ctl00_ctl00_c_c_CustomFields_CustomFieldKey24_dateInput_ClientState": {
                "enabled": True,
                "emptyMessage": "",
                "validationText": "",
                "valueAsString": "",
                "minDateStr": "1753-01-02-00-00-00",
                "maxDateStr": "9999-12-31-00-00-00",
                "lastSetTextBoxValue": ""
            },
            "ctl00_ctl00_c_c_CustomFields_CustomFieldKey24_calendar_SD": "[]",
            "ctl00_ctl00_c_c_CustomFields_CustomFieldKey24_calendar_AD": [
                [1753, 1, 2], [9999, 12, 31], cur_date_array
            ],
            "ctl00_ctl00_c_c_CustomFields_CustomFieldKey24_ClientState": {
                "minDateStr": "1753-01-02-00-00-00",
                "maxDateStr": "9999-12-31-00-00-00"
            },
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey35": "",
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey35$dateInput": "",
            "ctl00_ctl00_c_c_CustomFields_CustomFieldKey35_dateInput_ClientState": {
                "enabled": True,
                "emptyMessage": "",
                "validationText": "",
                "valueAsString": "",
                "minDateStr": "1753-01-02-00-00-00",
                "maxDateStr": "9999-12-31-00-00-00",
                "lastSetTextBoxValue": ""
            },
            "ctl00_ctl00_c_c_CustomFields_CustomFieldKey35_calendar_SD": "[]",
            "ctl00_ctl00_c_c_CustomFields_CustomFieldKey35_calendar_AD": [
                [1753, 1, 2], [9999, 12, 31], cur_date_array
            ],
            "ctl00_ctl00_c_c_CustomFields_CustomFieldKey35_ClientState": {
                "minDateStr": "1753-01-02-00-00-00",
                "maxDateStr": "9999-12-31-00-00-00"
            },
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey39": "",
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey47": "",
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey61": "",
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey68": "",
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey74": "",
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey76": "",
            "ctl00$ctl00$c$c$CustomFields$CustomFieldKey45": "",
            "ctl00$ctl00$c$c$CustomFields$hdnCustomFieldList": "",
            "ctl00_ctl00_c_c_SharedCalendar_SD": [[2025, 2, 10]],
            "ctl00_ctl00_c_c_SharedCalendar_AD": [
                [1980, 1, 1], [2099, 12, 30], [2025, 2, 1]
            ],
            "ctl00_ctl00_c_c_wipEdit_ClientState": "",
            "ctl00$ctl00$c$c$DeliveryAddressGrid$rwadditionaldeliveryaddress$C$hfDeliveryAddrKey": "",
            "ctl00$ctl00$c$c$DeliveryAddressGrid$rwadditionaldeliveryaddress$C$addressesGrid$ctl00$ctl03$ctl01$PageSizeComboBox": "5",
            "ctl00_ctl00_c_c_DeliveryAddressGrid_rwadditionaldeliveryaddress_C_addressesGrid_ctl00_ctl03_ctl01_PageSizeComboBox_ClientState": "",
            "ctl00_ctl00_c_c_DeliveryAddressGrid_rwadditionaldeliveryaddress_C_addressesGrid_ClientState": "",
            "ctl00_ctl00_c_c_DeliveryAddressGrid_rwadditionaldeliveryaddress_ClientState": "",
            "ctl00_ctl00_c_tsBot_ClientState": {
                "selectedIndexes": [
                    "0"
                ],
                "logEntries": [],
                "scrollState": {}
            },
            "ctl00_ctl00_c_wctl00_ctl00_c_c_MasterFacilityField_cbLookup_ClientState": "",
            "ctl00_ctl00_c_wctl00_ctl00_c_c_luTaxZone_cbLookup_ClientState": "",
            "ctl00_ctl00_c_wctl00_ctl00_c_c_luDeliveryTechnician_cbLookup_ClientState": "",
            "ctl00_ctl00_c_wctl00_ctl00_c_c_luLocation_cbLookup_ClientState": "",
            "ctl00_ctl00_c_wctl00_ctl00_c_c_luAssignedTo_cbLookup_ClientState": "",
            "ctl00_ctl00_c_rwmPE_ClientState": "",
            "ctl00_ctl00_c_concurrencyWin_ClientState": "",
            "ctl00_ctl00_c_delWinMstr_ClientState": "",
            "ctl00_ctl00_c_voidWinMstr_ClientState": "",
            "ctl00_ctl00_c_deliveryWinMstr_ClientState": "",
            "ctl00_ctl00_c_validationErrorWindow_ClientState": "",
            "ctl00_ctl00_c_authWinMstr_ClientState": "",
            "ctl00_ctl00_c_rwConnectWalkInPatient_ClientState": "",
            "ctl00_ctl00_c_eligibilityWindow_ClientState": "",
            "ctl00_ctl00_c_createCMNWinMstr_ClientState": "",
            "ctl00_ctl00_c_zeroKeyErrorWin_ClientState": "",
            "ctl00_ctl00_c_intakeRentalWarningWindow_ClientState": "",
            "ctl00_ctl00_c_rwmBTMaster_ClientState": "",
            "ctl00_ctl00_c_rwmInvitePatient_ClientState": "",
            "ctl00$ctl00$c$ucMainFooter$isReadOnly": False,
            "ctl00_ctl00_c_rttSOPatientBranchSecurity_ClientState": "",
            "__ASYNCPOST": True,
            "RadAJAXControlID": "ctl00_ctl00_c_pnlSOData1"
        }
        headers = self.headers.copy()
        headers["X-MicrosoftAjax"] = "Delta=true"
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"

        parsed_data = self._create_form_data(form_data=data)
        response = await self._make_request(method="POST", url=url, headers=headers, data=parsed_data)

        response = unquote(response)
        redirect_page = response.split('||')[2].replace('|', '')
        if "exception" in redirect_page.lower():
            raise IntegrationAPIError(
                integration_name="brightree",
                message=f"Failed to create new sales order: {redirect_page}",
                status_code=500
            )

        redirect_page = f"{self.url}{redirect_page}"
        key_part = redirect_page.split('SalesOrderKey=')[1]
        # Split on & and take the part before to remove any additional parameters
        order_key = key_part.split('&')[0]
        return {
            "sales_order_key": order_key,
            "sales_order_page": redirect_page,
        }

    @staticmethod
    def _clean_string(text: str) -> str:
        """
        Clean a string by removing excessive newlines and whitespace.

        Args:
            text (str): The input string to clean

        Returns:
            str: The cleaned string with normalized line breaks and whitespace
        """
        # Split on any combination of newlines
        lines = text.splitlines()

        # Clean each line and filter out empty ones
        cleaned_lines = [line.strip() for line in lines if line.strip()]

        # Join with single newlines
        return '\n'.join(cleaned_lines)

    @staticmethod
    def _combine_datetime(date_str: str, time_str: str) -> str:
        """
        Combine date (YYYY-MM-DD) and time (HH:MM AM/PM) into format YYYY-MM-DD-HH-MM-00

        Args:
            date_str: Date in YYYY-MM-DD format
            time_str: Time in HH:MM AM/PM format

        Returns:
            Combined datetime string in YYYY-MM-DD-HH-MM-00 format
        """
        if date_str == '':
            return ''

        dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %I:%M %p")
        return dt.strftime("%Y-%m-%d-%H-%M-00")
