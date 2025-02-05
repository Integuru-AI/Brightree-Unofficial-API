import aiohttp
import urllib.parse
from bs4 import BeautifulSoup
from typing import Any, Union
from fake_useragent import UserAgent
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
    def _extract_input_value(soup: BeautifulSoup, input_id: str) -> str:
        """
        Helper method to extract value from an input element by its ID
        """
        input_element = soup.find("input", {"id": input_id})
        return input_element.get("value") if input_element else None

    async def _get_create_patient_page(self):
        url = f"{self.url}/F1/02873/Nation/Patient/frmPatientPersonal.aspx?PatientKey=0&Edit=1"
        response = await self._make_request(method="GET", url=url, headers=self.headers)
        return response

    async def create_patient(self, first_name: str, last_name: str):
        url = f"{self.url}/F1/02873/Nation/Patient/frmPatientPersonal.aspx?PatientKey=0&Edit=1"
        page = await self._get_create_patient_page()
        soup = self._create_soup(page)

        view_state_val = self._extract_input_value(soup=soup, input_id="__VIEWSTATE")
        event_validation_val = self._extract_input_value(soup=soup, input_id="__EVENTVALIDATION")
        view_state_gen_val = self._extract_input_value(soup=soup, input_id="__VIEWSTATEGENERATOR")

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
            "ctl00_ctl00_c_btnContextSave_ClientState": "{\"text\":\"\",\"value\":\"\",\"checked\":false,\"target\":\"\",\"navigateUrl\":\"\",\"commandName\":\"Save\",\"commandArgument\":\"\",\"autoPostBack\":true,\"selectedToggleStateIndex\":0,\"validationGroup\":null,\"readOnly\":false,\"primary\":false,\"enabled\":true}",
            "ctl00_ctl00_c_btnSaveSplit_ClientState": "{\"text\":\"Save\",\"value\":\"\",\"checked\":false,\"target\":\"\",\"navigateUrl\":\"\",\"commandName\":\"\",\"commandArgument\":\"\",\"autoPostBack\":false,\"selectedToggleStateIndex\":0,\"validationGroup\":null,\"readOnly\":false,\"primary\":false,\"enabled\":true}",
            "ctl00_ctl00_c_btnNewSalesOrder_ClientState": "{\"text\":\"New Sales Order\",\"value\":\"\",\"checked\":false,\"target\":\"\",\"navigateUrl\":\"\",\"commandName\":\"\",\"commandArgument\":\"\",\"autoPostBack\":true,\"selectedToggleStateIndex\":0,\"validationGroup\":null,\"readOnly\":false,\"primary\":false,\"enabled\":false}",
            "ctl00_ctl00_c_btnPickup_ClientState": "{\"text\":\"New Pickup/Exchange\",\"value\":\"\",\"checked\":false,\"target\":\"\",\"navigateUrl\":\"\",\"commandName\":\"\",\"commandArgument\":\"\",\"autoPostBack\":true,\"selectedToggleStateIndex\":0,\"validationGroup\":null,\"readOnly\":false,\"primary\":false,\"enabled\":false}",
            "ctl00_ctl00_c_btnLaunch_btnLaunch_Menu_ClientState": "",
            "ctl00_ctl00_c_btnLaunch_btnLaunch_Button_ClientState": "{\"text\":\"Launch\",\"value\":\"\",\"checked\":false,\"target\":\"\",\"navigateUrl\":\"\",\"commandName\":\"\",\"commandArgument\":\"\",\"autoPostBack\":true,\"selectedToggleStateIndex\":0,\"validationGroup\":null,\"readOnly\":false,\"primary\":false,\"enabled\":true}",
            "ctl00_ctl00_c_PtAppRegControl_btnDoInvite_ClientState": "{\"text\":\"DoInvite\",\"value\":\"\",\"checked\":false,\"target\":\"\",\"navigateUrl\":\"\",\"commandName\":\"\",\"commandArgument\":\"\",\"autoPostBack\":true,\"selectedToggleStateIndex\":0,\"validationGroup\":null,\"readOnly\":false,\"primary\":false,\"enabled\":true}",
            "ctl00_ctl00_c_PtAppRegControl_btnDoPasswordReset_ClientState": "{\"text\":\"DoPasswordReset\",\"value\":\"\",\"checked\":false,\"target\":\"\",\"navigateUrl\":\"\",\"commandName\":\"\",\"commandArgument\":\"\",\"autoPostBack\":true,\"selectedToggleStateIndex\":0,\"validationGroup\":null,\"readOnly\":false,\"primary\":false,\"enabled\":true}",
            "ctl00_ctl00_c_PtAppRegControl_btnViewQRCode_ClientState": "{\"text\":\"DoInvite\",\"value\":\"\",\"checked\":false,\"target\":\"\",\"navigateUrl\":\"\",\"commandName\":\"\",\"commandArgument\":\"\",\"autoPostBack\":true,\"selectedToggleStateIndex\":0,\"validationGroup\":null,\"readOnly\":false,\"primary\":false,\"enabled\":true}",
            "ctl00$ctl00$c$ssnControl$hfSSNRetrieved": "false",
            "ctl00$ctl00$c$ssnControl$hfSSN": "",
            "ctl00$ctl00$c$hdnShowBanner": "",
            "ctl00$ctl00$c$hdnDMEScriptShowBanner": "",
            "ctl00_ctl00_c_tsTop_ClientState": "{\"selectedIndexes\":[\"1\"],\"logEntries\":[],\"scrollState\":{}}",

            "ctl00$ctl00$c$c$txtLastName": f"{last_name}",
            "ctl00$ctl00$c$c$txtFirstName": f"{first_name}",
            "ctl00$ctl00$c$c$txtMiddleName": "",
            "ctl00$ctl00$c$c$txtPreferredName": "",
            "ctl00$ctl00$c$c$txtSuffix": "",
            "ctl00$ctl00$c$c$hmeDOB": "",
            "ctl00$ctl00$c$c$hmeDOB$dateInput": "",

            "ctl00_ctl00_c_c_hmeDOB_dateInput_ClientState": "{\"enabled\":true,\"emptyMessage\":\"\","
                                                            "\"validationText\":\"\",\"valueAsString\":\"\","
                                                            "\"minDateStr\":\"1753-01-02-00-00-00\","
                                                            "\"maxDateStr\":\"9999-12-31-00-00-00\","
                                                            "\"lastSetTextBoxValue\":\"\"}",
            "ctl00_ctl00_c_c_hmeDOB_calendar_SD": "[]",
            "ctl00_ctl00_c_c_hmeDOB_calendar_AD": "[[1753,1,2],[9999,12,31],[2025,2,4]]",
            "ctl00_ctl00_c_c_hmeDOB_ClientState": "{\"minDateStr\":\"1753-01-02-00-00-00\","
                                                  "\"maxDateStr\":\"9999-12-31-00-00-00\"}",
            "ctl00$ctl00$c$c$ssnControl$hmeSSN": "___-__-____",
            "ctl00_ctl00_c_c_ssnControl_hmeSSN_ClientState": "{\"enabled\":true,\"emptyMessage\":\"\","
                                                             "\"validationText\":\"\","
                                                             "\"valueAsString\":\"___-__-____\","
                                                             "\"valueWithPromptAndLiterals\":\"___-__-____\","
                                                             "\"lastSetTextBoxValue\":\"___-__-____\"}",
            "ctl00$ctl00$c$c$ssnControl$currentSSNView$hfSSNRetrieved": "false",
            "ctl00$ctl00$c$c$ssnControl$currentSSNView$hfSSN": "",
            "ctl00$ctl00$c$c$ssnControl$tbSSNEdit$tb": "",
            "ctl00$ctl00$c$c$ssnControl$tbSSNConfirm$tb": "",
            "ctl00$ctl00$c$c$txtAccountNumber": "",
            "ctl00$ctl00$c$c$ddlCustomerType": "Patient",
            "ctl00$ctl00$c$c$txtPriorSystemKey": "",
            "ctl00$ctl00$c$c$MasterFacilityField$cbLookup_Input": "[None]",
            "ctl00$ctl00$c$c$MasterFacilityField$cbLookup_value": "0",
            "ctl00$ctl00$c$c$MasterFacilityField$cbLookup_text": "[None]",
            "ctl00$ctl00$c$c$MasterFacilityField$cbLookup_clientWidth": "150px",
            "ctl00$ctl00$c$c$MasterFacilityField$cbLookup_clientHeight": "14px",
            "ctl00_ctl00_c_c_btnCopyFacilityAddress_ClientState": "{\"text\":\"Copy Facility Address\","
                                                                  "\"value\":\"\",\"checked\":false,\"target\":\"\","
                                                                  "\"navigateUrl\":\"\",\"commandName\":\"\","
                                                                  "\"commandArgument\":\"\",\"autoPostBack\":true,"
                                                                  "\"selectedToggleStateIndex\":0,"
                                                                  "\"validationGroup\":null,\"readOnly\":false,"
                                                                  "\"primary\":false,\"enabled\":false}",
            "ctl00_ctl00_c_c_ucAlternateID_rdwManageAID_C_btnSaveAID_ClientState": "{\"text\":\"Save \","
                                                                                   "\"value\":\"\",\"checked\":false,"
                                                                                   "\"target\":\"\","
                                                                                   "\"navigateUrl\":\"\","
                                                                                   "\"commandName\":\"\","
                                                                                   "\"commandArgument\":\"\","
                                                                                   "\"autoPostBack\":true,"
                                                                                   "\"selectedToggleStateIndex\":0,"
                                                                                   "\"validationGroup\":null,"
                                                                                   "\"readOnly\":false,"
                                                                                   "\"primary\":false,"
                                                                                   "\"enabled\":true}",
            "ctl00_ctl00_c_c_ucAlternateID_rdwManageAID_C_btnDeleteAID_ClientState": "{\"text\":\"Delete\",\"value\":\"\",\"checked\":false,\"target\":\"\",\"navigateUrl\":\"\",\"commandName\":\"\",\"commandArgument\":\"\",\"autoPostBack\":true,\"selectedToggleStateIndex\":0,\"validationGroup\":null,\"readOnly\":false,\"primary\":false,\"enabled\":true}",
            "ctl00_ctl00_c_c_ucAlternateID_rdwManageAID_C_btnCancelAID_ClientState": "{\"text\":\"Cancel\",\"value\":\"\",\"checked\":false,\"target\":\"\",\"navigateUrl\":\"\",\"commandName\":\"\",\"commandArgument\":\"\",\"autoPostBack\":true,\"selectedToggleStateIndex\":0,\"validationGroup\":null,\"readOnly\":false,\"primary\":false,\"enabled\":true}",
            "ctl00_ctl00_c_c_ucAlternateID_rdwManageAID_ClientState": "",
            "ctl00_ctl00_c_c_ucAlternateID_rttAIDClear_ClientState": "",
            "ctl00_ctl00_c_c_btnCopyToInsured_ClientState": "{\"text\":\"Copy to Insured\",\"value\":\"\",\"checked\":false,\"target\":\"\",\"navigateUrl\":\"\",\"commandName\":\"\",\"commandArgument\":\"\",\"autoPostBack\":true,\"selectedToggleStateIndex\":0,\"validationGroup\":null,\"readOnly\":false,\"primary\":false,\"enabled\":true}",
            "ctl00$ctl00$c$c$ucBillingAddressUpdate$hfLobKey": "Basic bGl2ZV9wdWJfYTM4NTU1NjAyOTcyMzhiOTg0NzQwNDZmNzZmNDVmODo",
            "ctl00_ctl00_c_c_ucBillingAddressUpdate_rdwManagePBA_C_btnValidateAddress_ClientState": "{\"text\":\"Validate\",\"value\":\"\",\"checked\":false,\"target\":\"\",\"navigateUrl\":\"\",\"commandName\":\"\",\"commandArgument\":\"\",\"autoPostBack\":true,\"selectedToggleStateIndex\":0,\"validationGroup\":null,\"readOnly\":false,\"primary\":false,\"enabled\":true}",
            "ctl00_ctl00_c_c_ucBillingAddressUpdate_rdwManagePBA_C_btnCancelPBA_ClientState": "{\"text\":\"Cancel\",\"value\":\"\",\"checked\":false,\"target\":\"\",\"navigateUrl\":\"\",\"commandName\":\"\",\"commandArgument\":\"\",\"autoPostBack\":true,\"selectedToggleStateIndex\":0,\"validationGroup\":null,\"readOnly\":false,\"primary\":false,\"enabled\":true}",
            "ctl00$ctl00$c$c$ucBillingAddressUpdate$rdwManagePBA$C$acAddressLine1": "",
            "ctl00$ctl00$c$c$ucBillingAddressUpdate$rdwManagePBA$C$AddressLine2Field": "",
            "ctl00$ctl00$c$c$ucBillingAddressUpdate$rdwManagePBA$C$AddressLine3Field": "",
            "ctl00$ctl00$c$c$ucBillingAddressUpdate$rdwManagePBA$C$CityField": "",
            "ctl00$ctl00$c$c$ucBillingAddressUpdate$rdwManagePBA$C$pbStateField": "CA",
            "ctl00$ctl00$c$c$ucBillingAddressUpdate$rdwManagePBA$C$pbCountyField": "0",
            "ctl00$ctl00$c$c$ucBillingAddressUpdate$rdwManagePBA$C$pbCountryField": "1",
            "ctl00$ctl00$c$c$ucBillingAddressUpdate$rdwManagePBA$C$pbPostalCodeField": "_____-____",
            "ctl00_ctl00_c_c_ucBillingAddressUpdate_rdwManagePBA_C_pbPostalCodeField_ClientState": "{\"enabled\":true,\"emptyMessage\":\"\",\"validationText\":\"\",\"valueAsString\":\"_____-____\",\"valueWithPromptAndLiterals\":\"_____-____\",\"lastSetTextBoxValue\":\"_____-____\"}",
            "ctl00_ctl00_c_c_ucBillingAddressUpdate_rdwManagePBA_ClientState": "",
            "ctl00_ctl00_c_c_ucBillingAddressUpdate_rttPBAClear_ClientState": "",
            "ctl00$ctl00$c$c$hmePhone": "(___) ___-____",
            "ctl00_ctl00_c_c_hmePhone_ClientState": "{\"enabled\":true,\"emptyMessage\":\"\",\"validationText\":\"\",\"valueAsString\":\"(___) ___-____\",\"valueWithPromptAndLiterals\":\"(___) ___-____\",\"lastSetTextBoxValue\":\"(___) ___-____\"}",
            "ctl00$ctl00$c$c$hmeFax": "(___) ___-____",
            "ctl00_ctl00_c_c_hmeFax_ClientState": "{\"enabled\":true,\"emptyMessage\":\"\",\"validationText\":\"\",\"valueAsString\":\"(___) ___-____\",\"valueWithPromptAndLiterals\":\"(___) ___-____\",\"lastSetTextBoxValue\":\"(___) ___-____\"}",
            "ctl00$ctl00$c$c$hmeMobilePhone": "(___) ___-____",
            "ctl00_ctl00_c_c_hmeMobilePhone_ClientState": "{\"enabled\":true,\"emptyMessage\":\"\",\"validationText\":\"\",\"valueAsString\":\"(___) ___-____\",\"valueWithPromptAndLiterals\":\"(___) ___-____\",\"lastSetTextBoxValue\":\"(___) ___-____\"}",
            "ctl00$ctl00$c$c$txtEmailAddress": "",
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
            "ctl00_ctl00_c_c_txtDiscountPercent_ClientState": "{\"enabled\":false,\"emptyMessage\":\"\",\"validationText\":\"0\",\"valueAsString\":\"0\",\"minValue\":0,\"maxValue\":100,\"lastSetTextBoxValue\":\"0%\"}",
            "ctl00$ctl00$c$c$luTaxZone$cbLookup_Input": "[None]",
            "ctl00$ctl00$c$c$luTaxZone$cbLookup_value": "0",
            "ctl00$ctl00$c$c$luTaxZone$cbLookup_text": "[None]",
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
            "ctl00_ctl00_c_c_rdpDateOfAdmission_dateInput_ClientState": "{\"enabled\":true,\"emptyMessage\":\"\",\"validationText\":\"\",\"valueAsString\":\"\",\"minDateStr\":\"1753-01-02-00-00-00\",\"maxDateStr\":\"9999-12-31-00-00-00\",\"lastSetTextBoxValue\":\"\"}",
            "ctl00_ctl00_c_c_rdpDateOfAdmission_calendar_SD": "[]",
            "ctl00_ctl00_c_c_rdpDateOfAdmission_calendar_AD": "[[1753,1,2],[9999,12,31],[2025,2,4]]",
            "ctl00_ctl00_c_c_rdpDateOfAdmission_ClientState": "{\"minDateStr\":\"1753-01-02-00-00-00\",\"maxDateStr\":\"9999-12-31-00-00-00\"}",
            "ctl00$ctl00$c$c$rdpDateOfDischarge": "",
            "ctl00$ctl00$c$c$rdpDateOfDischarge$dateInput": "",
            "ctl00_ctl00_c_c_rdpDateOfDischarge_dateInput_ClientState": "{\"enabled\":true,\"emptyMessage\":\"\",\"validationText\":\"\",\"valueAsString\":\"\",\"minDateStr\":\"1753-01-02-00-00-00\",\"maxDateStr\":\"9999-12-31-00-00-00\",\"lastSetTextBoxValue\":\"\"}",
            "ctl00_ctl00_c_c_rdpDateOfDischarge_calendar_SD": "[]",
            "ctl00_ctl00_c_c_rdpDateOfDischarge_calendar_AD": "[[1753,1,2],[9999,12,31],[2025,2,4]]",
            "ctl00_ctl00_c_c_rdpDateOfDischarge_ClientState": "{\"minDateStr\":\"1753-01-02-00-00-00\",\"maxDateStr\":\"9999-12-31-00-00-00\"}",
            "ctl00$ctl00$c$c$chkActiveAddress": "on",
            "ctl00_ctl00_c_c_btnAdditionDeliveryAddress_ClientState": "{\"text\":\"Additional Address\",\"value\":\"\",\"checked\":false,\"target\":\"\",\"navigateUrl\":\"\",\"commandName\":\"\",\"commandArgument\":\"\",\"autoPostBack\":true,\"selectedToggleStateIndex\":0,\"validationGroup\":null,\"readOnly\":false,\"primary\":false,\"enabled\":false}",
            "ctl00$ctl00$c$c$ucAdditionalDeliveryAddress$hfLobKey": "Basic bGl2ZV9wdWJfYTM4NTU1NjAyOTcyMzhiOTg0NzQwNDZmNzZmNDVmODo",
            "ctl00_ctl00_c_c_ucAdditionalDeliveryAddress_rdwManagePBA_C_btnValidateAddress_ClientState": "{\"text\":\"Validate\",\"value\":\"\",\"checked\":false,\"target\":\"\",\"navigateUrl\":\"\",\"commandName\":\"\",\"commandArgument\":\"\",\"autoPostBack\":true,\"selectedToggleStateIndex\":0,\"validationGroup\":null,\"readOnly\":false,\"primary\":false,\"enabled\":true}",
            "ctl00_ctl00_c_c_ucAdditionalDeliveryAddress_rdwManagePBA_C_btnCancelPBA_ClientState": "{\"text\":\"Cancel\",\"value\":\"\",\"checked\":false,\"target\":\"\",\"navigateUrl\":\"\",\"commandName\":\"\",\"commandArgument\":\"\",\"autoPostBack\":true,\"selectedToggleStateIndex\":0,\"validationGroup\":null,\"readOnly\":false,\"primary\":false,\"enabled\":true}",
            "ctl00$ctl00$c$c$ucAdditionalDeliveryAddress$rdwManagePBA$C$acAddressLine1": "",
            "ctl00$ctl00$c$c$ucAdditionalDeliveryAddress$rdwManagePBA$C$AddressLine2Field": "",
            "ctl00$ctl00$c$c$ucAdditionalDeliveryAddress$rdwManagePBA$C$AddressLine3Field": "",
            "ctl00$ctl00$c$c$ucAdditionalDeliveryAddress$rdwManagePBA$C$CityField": "",
            "ctl00$ctl00$c$c$ucAdditionalDeliveryAddress$rdwManagePBA$C$pbStateField": "CA",
            "ctl00$ctl00$c$c$ucAdditionalDeliveryAddress$rdwManagePBA$C$pbCountyField": "0",
            "ctl00$ctl00$c$c$ucAdditionalDeliveryAddress$rdwManagePBA$C$pbCountryField": "1",
            "ctl00$ctl00$c$c$ucAdditionalDeliveryAddress$rdwManagePBA$C$pbPostalCodeField": "_____-____",
            "ctl00_ctl00_c_c_ucAdditionalDeliveryAddress_rdwManagePBA_C_pbPostalCodeField_ClientState": "{\"enabled\":true,\"emptyMessage\":\"\",\"validationText\":\"\",\"valueAsString\":\"_____-____\",\"valueWithPromptAndLiterals\":\"_____-____\",\"lastSetTextBoxValue\":\"_____-____\"}",
            "ctl00_ctl00_c_c_ucAdditionalDeliveryAddress_rdwManagePBA_ClientState": "",
            "ctl00_ctl00_c_c_ucAdditionalDeliveryAddress_rttPBAClear_ClientState": "",
            "ctl00$ctl00$c$c$PrimaryDeliveryAddress$i0$hfPtPrimaryDeliveryAddrKey": "",
            "ctl00_ctl00_c_c_PrimaryDeliveryAddress_i0_btnSameAsBillingAddressPrimary_ClientState": "{\"text\":\"Same as Billing Address\",\"value\":\"\",\"checked\":false,\"target\":\"\",\"navigateUrl\":\"\",\"commandName\":\"\",\"commandArgument\":\"\",\"autoPostBack\":true,\"selectedToggleStateIndex\":0,\"validationGroup\":null,\"readOnly\":false,\"primary\":false,\"enabled\":true}",
            "ctl00$ctl00$c$c$PrimaryDeliveryAddress$i0$ucPrimaryAddressUpdate$hfLobKey": "Basic bGl2ZV9wdWJfYTM4NTU1NjAyOTcyMzhiOTg0NzQwNDZmNzZmNDVmODo",
            "ctl00_ctl00_c_c_PrimaryDeliveryAddress_i0_ucPrimaryAddressUpdate_rdwManagePBA_C_btnValidateAddress_ClientState": "{\"text\":\"Validate\",\"value\":\"\",\"checked\":false,\"target\":\"\",\"navigateUrl\":\"\",\"commandName\":\"\",\"commandArgument\":\"\",\"autoPostBack\":true,\"selectedToggleStateIndex\":0,\"validationGroup\":null,\"readOnly\":false,\"primary\":false,\"enabled\":true}",
            "ctl00_ctl00_c_c_PrimaryDeliveryAddress_i0_ucPrimaryAddressUpdate_rdwManagePBA_C_btnCancelPBA_ClientState": "{\"text\":\"Cancel\",\"value\":\"\",\"checked\":false,\"target\":\"\",\"navigateUrl\":\"\",\"commandName\":\"\",\"commandArgument\":\"\",\"autoPostBack\":true,\"selectedToggleStateIndex\":0,\"validationGroup\":null,\"readOnly\":false,\"primary\":false,\"enabled\":true}",
            "ctl00$ctl00$c$c$PrimaryDeliveryAddress$i0$ucPrimaryAddressUpdate$rdwManagePBA$C$acAddressLine1": "",
            "ctl00$ctl00$c$c$PrimaryDeliveryAddress$i0$ucPrimaryAddressUpdate$rdwManagePBA$C$AddressLine2Field": "",
            "ctl00$ctl00$c$c$PrimaryDeliveryAddress$i0$ucPrimaryAddressUpdate$rdwManagePBA$C$AddressLine3Field": "",
            "ctl00$ctl00$c$c$PrimaryDeliveryAddress$i0$ucPrimaryAddressUpdate$rdwManagePBA$C$CityField": "",
            "ctl00$ctl00$c$c$PrimaryDeliveryAddress$i0$ucPrimaryAddressUpdate$rdwManagePBA$C$pbStateField": "CA",
            "ctl00$ctl00$c$c$PrimaryDeliveryAddress$i0$ucPrimaryAddressUpdate$rdwManagePBA$C$pbCountyField": "0",
            "ctl00$ctl00$c$c$PrimaryDeliveryAddress$i0$ucPrimaryAddressUpdate$rdwManagePBA$C$pbCountryField": "1",
            "ctl00$ctl00$c$c$PrimaryDeliveryAddress$i0$ucPrimaryAddressUpdate$rdwManagePBA$C$pbPostalCodeField": "_____-____",
            "ctl00_ctl00_c_c_PrimaryDeliveryAddress_i0_ucPrimaryAddressUpdate_rdwManagePBA_C_pbPostalCodeField_ClientState": "{\"enabled\":true,\"emptyMessage\":\"\",\"validationText\":\"\",\"valueAsString\":\"_____-____\",\"valueWithPromptAndLiterals\":\"_____-____\",\"lastSetTextBoxValue\":\"_____-____\"}",
            "ctl00_ctl00_c_c_PrimaryDeliveryAddress_i0_ucPrimaryAddressUpdate_rdwManagePBA_ClientState": "",
            "ctl00_ctl00_c_c_PrimaryDeliveryAddress_i0_ucPrimaryAddressUpdate_rttPBAClear_ClientState": "",
            "ctl00$ctl00$c$c$PrimaryDeliveryAddress$i0$TxtDescription": "",
            "ctl00_ctl00_c_c_PrimaryDeliveryAddress_i0_TxtDescription_ClientState": "{\"enabled\":true,\"emptyMessage\":\"\",\"validationText\":\"\",\"valueAsString\":\"\",\"lastSetTextBoxValue\":\"\"}",
            "ctl00$ctl00$c$c$PrimaryDeliveryAddress$i0$hmeDelPrimaryPhone": "(___) ___-____",
            "ctl00_ctl00_c_c_PrimaryDeliveryAddress_i0_hmeDelPrimaryPhone_ClientState": "{\"enabled\":true,\"emptyMessage\":\"\",\"validationText\":\"\",\"valueAsString\":\"(___) ___-____\",\"valueWithPromptAndLiterals\":\"(___) ___-____\",\"lastSetTextBoxValue\":\"(___) ___-____\"}",
            "ctl00$ctl00$c$c$PrimaryDeliveryAddress$i0$ddlZonePrimary": "0",
            "ctl00_ctl00_c_c_PrimaryDeliveryAddress_ClientState": "{\"expandedItems\":[\"0\"],\"logEntries\":[],\"selectedItems\":[\"0\"]}",
            "ctl00_ctl00_c_c_rwUpdatePHEmail_ClientState": "",
            "ctl00_ctl00_c_c_rwOptInStatusPopup_ClientState": "",
            "ctl00_ctl00_c_c_rwAdditionalAddress_ClientState": "",
            "ctl00_ctl00_c_tsBot_ClientState": "{\"selectedIndexes\":[\"1\"],\"logEntries\":[],\"scrollState\":{}}",
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
            "ctl00$ctl00$c$ucMainFooter$isReadOnly": "False",
            "__ASYNCPOST": "true",
            "RadAJAXControlID": "ctl00_ctl00_pageRAM"
        }

        headers = self.headers.copy()
        headers["X-MicrosoftAjax"] = "Delta=true"
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=utf-8"

        response = await self._make_request(method="POST", url=url, headers=headers, data=form_data)
        return response
