"""DOM SELECTOR MAP for the Lombardy Region Health Record (Fascicolo Sanitario) portal.

Each selector is a (strategy, value) tuple. Valid strategies:
    "id", "name", "css", "xpath", "class"

Convention: a selector can be a tuple or a list of tuples
(tried in sequence as fallbacks) for resilience.
"""
from __future__ import annotations

# ------------------------------------------------------------------
# Login SPID — Lombardy Region IdPC (/PublisherMetadata/SSOService)
# ------------------------------------------------------------------
LOGIN_SPID = {
    "url_accedi": "https://www.fascicolosanitario.regione.lombardia.it/web/areaprivata/homepage",
    "url_idpc": "https://idpcwrapper.crs.lombardia.it/PublisherMetadata/SSOService",
    # Form submitting to chosen IdP
    "form_spid": [("id", "SPIDform")],
    "field_indexIdp": [("id", "indexIdp")],
    # IdP index list verified on Lombardy Region IdPC
    "idp": {
        "Infocert": 0, "Register": 1, "Lepida": 2, "IntesiGroupSpid": 3,
        "Aruba": 4, "Namirial": 5, "Poste": 6, "InfoCamere": 7,
        "Tim": 8, "Sielte": 9, "TeamSystemId": 10, "EtnaID": 11,
    },
    # Selector for provider boxes (click sets indexIdp and submits)
    "box_idp": [("css", "a.home-box-fornitore")],
}

# ------------------------------------------------------------------
# Login CIE — Electronic Identity Card (Ministry of the Interior)
# ------------------------------------------------------------------
LOGIN_CIE = {
    "url_landing": "https://idserver.servizicie.interno.gov.it/idp/login/livello2",
    "url_matcher": "idserver.servizicie.interno.gov.it",
    "btn_entra_cie": [
        ("css", "form[action*='AuthRequestCieService']"),
        ("css", ".pulsante-cie form"),
        ("css", ".pulsante-cie"),
        ("css", "a.button-cie"),
        ("css", "[cie-button]"),
        ("css", "#cie-btn"),
        ("xpath", "//a[contains(@href, 'cie') or contains(@href, 'CIE') or contains(@href, 'Cie')]"),
        ("xpath", "//button[contains(., 'CIE') or contains(., 'Carta')]"),
    ],
    # Mode selection on idserver (Level 1/2 credentials vs smartphone/QR vs computer)
    "btn_credenziali": [
        ("css", "a[href*='livello2']"),
        ("css", "a[href*='credenziali']"),
        ("css", "button[id*='credenziali']"),
        ("xpath", "//a[contains(., 'credenziali') or contains(., 'Livello 1 e 2') or contains(., 'Entra con credenziali')]"),
        ("xpath", "//button[contains(., 'credenziali') or contains(., 'Livello 1 e 2') or contains(., 'Entra con credenziali')]"),
    ],
    "btn_smartphone": [
        ("css", "a[href*='login/app']"),
        ("xpath", "//a[contains(., 'smartphone') or contains(., 'CieID')]"),
        ("xpath", "//button[contains(., 'smartphone') or contains(., 'CieID')]"),
    ],
    "btn_computer": [
        ("xpath", "//a[contains(., 'computer') or contains(., 'Smart') or contains(., 'smartcard')]"),
        ("xpath", "//button[contains(., 'computer') or contains(., 'Smart') or contains(., 'smartcard')]"),
    ],
    # Level 1 and 2 credentials form
    "form": [("id", "loginUP"), ("css", "form#loginUP"), ("css", "form[action*='login']")],
    "username": [
        ("id", "username"),
        ("name", "username"),
        ("name", "codiceFiscale"),
        ("id", "codiceFiscale"),
        ("name", "j_username"),
    ],
    "password": [
        ("id", "password"),
        ("name", "password"),
        ("name", "j_password"),
        ("css", "input[type='password']"),
    ],
    "btn_prosegui": [
        ("css", "form#loginUP button[type='submit']"),
        ("css", "button[type='submit']"),
        ("css", "input[type='submit']"),
        ("id", "prosegui"),
        ("xpath", "//button[contains(., 'Procedi') or contains(., 'Prosegui') or contains(., 'Entra')]"),
    ],
    "link_app_cie": [("css", "a[href*='login/app']")],
    # Second factor: OTP (SMS or app)
    "otp": [
        ("id", "otp"),
        ("name", "otp"),
        ("id", "codiceOtp"),
        ("name", "codiceOtp"),
        ("name", "smsOtp"),
        ("css", "input[autocomplete='one-time-code']"),
        ("css", "input[type='number']:not(#username)"),
        ("css", "input[type='tel']:not(#username)"),
    ],
    "btn_otp": [
        ("css", "button[type='submit']"),
        ("css", "input[type='submit']"),
        ("id", "confermaOtp"),
        ("xpath", "//button[contains(., 'Verifica') or contains(., 'Conferma') or contains(., 'Invia') or contains(., 'Procedi')]"),
    ],
    "btn_richiedi_sms_otp": [
        ("xpath", "//*[contains(., 'Ricevi codice via SMS') or contains(., 'Invia OTP via SMS') or contains(., 'SMS')]"),
    ],
    # Switch to QR Code view (when push notification does not arrive or as alternative)
    "btn_mostra_qr": [
        ("xpath", "//a[contains(., 'QR') or contains(., 'qr') or contains(., 'Inquadra') or contains(., 'inquadra')]"),
        ("xpath", "//button[contains(., 'QR') or contains(., 'qr') or contains(., 'Inquadra') or contains(., 'inquadra')]"),
        ("xpath", "//*[contains(text(), 'Non hai ricevuto') or contains(text(), 'non hai ricevuto')]//ancestor::a"),
        ("xpath", "//*[contains(text(), 'Non hai ricevuto') or contains(text(), 'non hai ricevuto')]//ancestor::button"),
        ("xpath", "//*[contains(text(), 'codice QR') or contains(text(), 'Codice QR')]//ancestor::a"),
        ("xpath", "//*[contains(text(), 'codice QR') or contains(text(), 'Codice QR')]//ancestor::button"),
        ("css", "#qrGeneratorWrapper a"),
        ("css", "a[href*='qr']"),
        ("css", "a[href*='login/app']"),
        ("css", "button[id*='qr']"),
        ("css", "a[id*='qr']"),
        ("css", "#btn-qrcode"),
        ("css", ".btn-qrcode"),
    ],
    # Proceed post QR Code scanning
    "btn_post_qr": [
        ("css", "#postQRLink a"),
        ("css", "a[href*='livello1e2postqrcode']"),
        ("xpath", "//a[contains(@href, 'postqrcode') or contains(., 'dopo aver autenticato')]"),
    ],
    # QR Code for CieID app
    "qr_code": [
        ("css", "#qrFigure img"),
        ("css", "#qrWrapperImg img"),
        ("css", "#qrWrapper img"),
        ("css", "figure#qrFigure img"),
        ("css", "#qrcode img"),
        ("css", ".qr-code img"),
        ("css", "#qrcode-canvas"),
        ("css", "#qrcode canvas"),
        ("css", ".qr-code canvas"),
        ("css", "canvas.qrcode"),
        ("css", "img.qrcode"),
        ("css", "img[src*='qr']"),
        ("css", "img[src*='QR']"),
        ("css", "svg.qrcode"),
        ("css", "#qrcode svg"),
        ("css", "#qrcode"),
        ("css", ".qr-code"),
        ("css", "[id*='qrcode']"),
        ("xpath", "//img[contains(@alt, 'QR') or contains(@alt, 'qr') or contains(@alt, 'Qr')]"),
    ],
    # Pending push notification
    "push_waiting": [
        ("xpath", "//*[contains(., 'notifica') and (contains(., 'inviata') or contains(., 'apri') or contains(., 'CieID'))]"),
    ],
    # Consent screen for data transmission (CIE IdP or Regional portal)
    "consenso": [
        ("css", "button[name='confirm']"),
        ("css", "button[name='_eventId_proceed']"),
        ("css", "button[type='submit']"),
        ("xpath", "//button[contains(., 'Autorizza') or contains(., 'Acconsento') or contains(., 'Conferma') or contains(., 'Prosegui')]"),
        ("xpath", "//input[@type='submit' and (contains(@value, 'Autorizza') or contains(@value, 'Acconsento') or contains(@value, 'Conferma'))]"),
    ],
}

# ------------------------------------------------------------------
# SPID PosteID (Poste Italiane)
# ------------------------------------------------------------------
LOGIN_IDP_POSTEID = {
    "url_matcher": "posteid.poste.it",
    "form": [("css", "form[action*='xloginbasic']"), ("css", "form[name='login']")],
    "username": [("id", "username"), ("name", "username")],
    "password": [("id", "password"), ("name", "password")],
    "btn_avanti": [("css", "form[action*='xloginbasic'] button[type='submit']"), ("css", "button[type='submit']")],
    "form_qr": [("css", "form[action*='xqrlogindis']")],
    "otp": [("id", "otp"), ("name", "otp")],
    "btn_otp": [("css", "button[type=submit], input[type=submit]")],
    "consenso": [
        ("css", "button[name='confirm']"),
        ("css", "input[value*='Autorizza']"),
        ("xpath", "//button[contains(., 'Autorizza') or contains(., 'Conferma') or contains(., 'Prosegui')]"),
    ],
}

# ------------------------------------------------------------------
# SPID Aruba ID
# ------------------------------------------------------------------
LOGIN_IDP_ARUBA = {
    "url_matcher": "loginspid.aruba.it",
    "form": [("id", "spid-login")],
    "username": [("id", "username"), ("name", "username")],
    "password": [("id", "password"), ("name", "password")],
    "user_key": [("id", "userKey"), ("name", "userKey")],
    "btn_avanti": [("css", "#spid-login button[type='submit']"), ("css", "button[type='submit']")],
    "consenso": [
        ("css", "button[name*='confirm']"),
        ("xpath", "//button[contains(., 'Autorizza') or contains(., 'Conferma') or contains(., 'Prosegui')]"),
    ],
}

# ------------------------------------------------------------------
# SPID InfoCert ID
# ------------------------------------------------------------------
LOGIN_IDP_INFOCERT = {
    "url_matcher": "identity.infocert.it",
    "form": [("id", "spid-login")],
    "username": [("id", "nome_utente"), ("name", "nome_utente")],
    "password": [("id", "password"), ("name", "password")],
    "btn_avanti": [("css", "#spid-login button[type='submit']"), ("css", "button[type='submit']")],
    "consenso": [
        ("css", "button[type='submit']"),
        ("xpath", "//button[contains(., 'Autorizza') or contains(., 'Conferma') or contains(., 'Prosegui')]"),
    ],
}

# ------------------------------------------------------------------
# SPID Lepida ID
# ------------------------------------------------------------------
LOGIN_IDP_LEPIDA = {
    "url_matcher": "id.lepida.it",
    "form": [("id", "spid-login")],
    "username": [("id", "username"), ("name", "j_username")],
    "password": [("id", "password"), ("name", "j_password")],
    "btn_avanti": [("id", "btn-spid-login"), ("css", "button[type='submit']")],
    "btn_qr": [("id", "btn_proceed_qr_login")],
    "consenso": [
        ("css", "button[name='_eventId_proceed']"),
        ("xpath", "//button[contains(., 'Autorizza') or contains(., 'Conferma') or contains(., 'Prosegui')]"),
    ],
}

# ------------------------------------------------------------------
# SPID Namirial ID
# ------------------------------------------------------------------
LOGIN_IDP_NAMIRIAL = {
    "url_matcher": "spid.namirial.it",
    "form": [("id", "kc-form-login")],
    "username": [("id", "username"), ("name", "username")],
    "password": [("id", "password"), ("name", "password")],
    "btn_avanti": [("id", "kc-login"), ("css", "input[type='submit']")],
    "consenso": [
        ("css", "input[type='submit']"),
        ("xpath", "//input[contains(@value, 'Autorizza') or contains(@value, 'Conferma')]"),
    ],
}

# ------------------------------------------------------------------
# IdP: SielteID
# STEP 1 — loginform (identity.sieltecloud.it/simplesaml/.../loginform.php)
# username = Tax Code; after submit the IdP requests confirmation via app/OTP.
# ------------------------------------------------------------------
LOGIN_IDP_SIELTEID = {
    "url_matcher": "identity.sieltecloud.it",
    "username": [("id", "username"), ("name", "username")],
    "password": [("id", "password"), ("name", "password")],
    "form": [("id", "piLoginForm")],
    "btn_prosegui": [("id", "autorizza"), ("css", "button[type='submit']")],
    "field_authstate": [("name", "AuthState")],
    "scelta_metodo": {
        "url_matcher": "Scegli il metodo",
        "notifica": [("eval", "useNotify()"), ("id", "usenotify"), ("css", "#usenotify, button[onclick*='useNotify'], a[onclick*='useNotify']")],
        "otp_app": [("eval", "useAPP()"), ("id", "useapp"), ("css", "#useapp, button[onclick*='useAPP'], a[onclick*='useAPP']")],
        "field_usenotify": [("id", "usenotify")],
        "field_useapp": [("id", "useapp")],
    },
    "otp": [
        ("id", "otp"),
        ("name", "otp"),
        ("id", "currentOtp"),
        ("name", "currentOtp"),
        ("css", "input[name*='otp' i]"),
        ("css", "input[id*='otp' i]"),
        ("css", "form#piLoginForm input[type='password']"),
        ("css", "form#piLoginForm input[type='number']"),
        ("css", "form#piLoginForm input[type='text']:not(#username)"),
    ],
    "btn_otp": [
        ("id", "autorizza"),
        ("css", "form#piLoginForm button[type='submit']"),
        ("css", "form#piLoginForm input[type='submit']"),
        ("css", "button[type='submit']"),
        ("css", "input[type='submit']"),
    ],
    "consenso": {
        "url_matcher": "Autorizza",
        "btn_autorizza": [
            ("css", "form#piLoginForm button[type='submit']"),
            ("css", "button[name='confirm']"),
            ("xpath", "//button[contains(., 'Autorizza') or contains(., 'Conferma') or contains(., 'Invia')]"),
        ],
        "field_accept": [("id", "accept")],
    },
    "post_login_url": "https://www.fascicolosanitario.regione.lombardia.it/web/areaprivata/ricette",
}

# ------------------------------------------------------------------
# Prescriptions Page (/web/areaprivata/ricette) — list of bookable prescriptions
# ------------------------------------------------------------------
RICETTE = {
    "url": "https://www.fascicolosanitario.regione.lombardia.it/web/areaprivata/ricette",
    # Filter checkboxes
    "filtro_specialistiche": [("id", "specialisticheID")],
    "filtro_prescritte": [("id", "prescritteID")],
    # Each prescription card 'Prenota' links to prenotaonline with token hash
    "card_prenota": [("css", "a.cambia-visibilita[href*='prenotaonline']")],
}

# ------------------------------------------------------------------
# prenotaonline App — Single Page Application (area menu + availability list)
# ------------------------------------------------------------------
PRENOTAONLINE = {
    "url": "https://www.fascicolosanitario.regione.lombardia.it/prenotaonline/le-tue-ricette",
    # --- Step 1: select prescription -> click 'Prenota' on specialist visit ---
    "btn_prenota_ricetta": [("css", "button[ng-click*='prenotaAppuntamento']")],
    # --- Modal 'Completa dati' (CONTROL/FOLLOW-UP?): radio Yes/No + Confirm ---
    "modal_completa_dati": {
        "radio_si": [("css", "input[name=controllo][value=true]")],
        "radio_no": [("css", "input[name=controllo][value=false]")],
        # Note: actual Angular controller is prenotaCompletaDatiCtrl (capital C)
        "btn_conferma": [("css", "button[ng-click*='CompletaDatiCtrl.conferma']")],
    },
    # --- Informational modal to dismiss ---
    "modal_info_chiudi": [("css", "button[ng-click*='messaggiCtrl.close']")],
    # --- Step 2: Where/When ---
    "sel_provincia": [("id", "provincia")],
    # Available provinces in dropdown
    "province": [
        "BERGAMO", "BRESCIA", "COMO", "CREMONA", "LECCO", "LODI",
        "MANTOVA", "MILANO CITTA'", "MILANO PROVINCIA", "MONZA E DELLA BRIANZA",
        "PAVIA", "SONDRIO", "VARESE",
    ],
    "input_data_dal": [("id", "quando")],
    "input_telefono": [("id", "telefono")],
    "input_email": [("id", "email")],
    "checkbox_consenso": [("id", "consensoPrenotazione")],
    # Confirm button after contact info taking to availability list
    "btn_conferma_dove_quando": [("css", "button[ng-click*='doveQuandoCtrl.conferma']"), ("xpath", "//button[contains(., 'Conferma')]")],
    # Button submitting search after Where/When (enabled only when form is valid)
    "btn_ricerca_disponibilita": [("css", "button[ng-click*='doveQuandoCtrl.ricercaDisponibilita']")],
    # --- Step 3: AVAILABILITY LIST ---
    # Buttons appearing both in results view and modification modal
    "btn_modifica_ricerca": [("css", "button[ng-click*='prenotaDisponibilitaCtrl.modificaCriteri']")],  # Opens modal
    "btn_aggiorna_ricerca": [("css", "button[ng-click*='doveQuandoModalCtrl.aggiorna']")],          # In modal, refreshes search
    "btn_chiudi_modale_dove": [("css", "button[ng-click*='doveQuandoModalCtrl.chiudi']")],
    # --- Modal 'Modifica ricerca': configurable criteria ---
    "giorni_settimana": {
        "tutti": [("id", "tutti")],
        "lun": [("id", "lun")], "mar": [("id", "mar")], "mer": [("id", "mer")],
        "gio": [("id", "gio")], "ven": [("id", "ven")], "sab": [("id", "sab")],
        "dom": [("id", "dom")],
        # ng-model used by Angular (for proper binding)
        "model": ["doveQuandoCtrl.criteria.vincoliTemporali.lunedi",
                   "doveQuandoCtrl.criteria.vincoliTemporali.martedi",
                   "doveQuandoCtrl.criteria.vincoliTemporali.mercoledi",
                   "doveQuandoCtrl.criteria.vincoliTemporali.giovedi",
                   "doveQuandoCtrl.criteria.vincoliTemporali.venerdi",
                   "doveQuandoCtrl.criteria.vincoliTemporali.sabato",
                   "doveQuandoCtrl.criteria.vincoliTemporali.domenica"],
    },
    "fascia_oraria": {
        "tutte": [("id", "tutte")],
        "mattina": [("id", "mattina")],
        "pomeriggio": [("id", "pomeriggio")],
        "model": ["doveQuandoCtrl.hasFascieTutte",
                   "doveQuandoCtrl.criteria.vincoliTemporali.mattina",
                   "doveQuandoCtrl.criteria.vincoliTemporali.pomeriggio"],
    },
    "rgiorni_model_map": {
        "lun": "lunedi", "mar": "martedi", "mer": "mercoledi",
        "gio": "giovedi", "ven": "venerdi", "sab": "sabato", "dom": "domenica"
    },
    "lista_disponibilita": [("css", "[ng-repeat*='in disponibilitaCtrl.valori']")],
    # Single slot block (date/time + healthcare provider + facility)
    "slot_disponibilita": [("css", "[ng-repeat*='disponibilita in disponibilitaCtrl.valori']")],
    # Fields within each slot
    "slot_data_ora": [("css", "span[ng-if*='isClassico']")],              # 'DD/MM/YYYY - HH:MM'
    "slot_azienda": [("css", "[ng-repeat*='disponibilita in disponibilitaCtrl.valori'] .appuntamento-field-value span")],
    "slot_none_msg": [("xpath", "//h5[contains(., 'Non sono state trovate disponibilità')]")],
    # 'Verifica e conferma' button for a slot
    "btn_verifica_conferma": [("css", "button[ng-click*='setAzioneRichiestaConfermaDisponibilita']")],
    # --- Modal booking confirmation (after 'Verifica e conferma') ---
    "modal_conferma_prenota": {
        "checkbox_lettura": [("id", "presaVisioneNote")],
        "btn_conferma": [("css", "button[ng-click*='verificaPrenotazioneCtrl.conferma']")],
        "btn_annulla": [("css", "button[ng-click*='verificaPrenotazioneCtrl.annulla']")],
    },
    # Booking success screen
    "esito_successo": [("xpath", "//*[contains(., 'Prenotazione effettuata con successo')]")],
    "esito_codice": [("xpath", "//*[contains(., 'Codice prenotazione')]")],
    # Confirmed appointment area (for cancellation/update)
    "btn_annulla_appuntamento": [("xpath", "//button[contains(., 'Annulla appuntamento')]")],
    # Buttons for more dates/times
    "btn_ricerca_altre_date": [("css", "button[ng-click*='ricercaUlterioriDisponibilita'][ng-click*=\"'D'\"]")],
    "btn_ricerca_altri_orari": [("css", "button[ng-click*='ricercaUlterioriDisponibilita'][ng-click*=\"'O'\"]")],
}

# ------------------------------------------------------------------
# Booking agenda section
# ------------------------------------------------------------------
BOOKING = {
    # Menu entry / link to open bookings
    "apri_prenotazioni": [("css", "a[href*='prenotazioni'], a[aria-label*='prenotazione']")],
    # For Flow A: prescription dropdown/field to select
    "sel_ricetta": [("css", "select[name*='ricetta'], #ricetta, [data-testid*='ricetta']")],
    # Specialty / area dropdown
    "sel_area": [("css", "select[name*='area'], #area, [data-testid*='area']")],
    # Button that loads availability page
    "btn_cerca": [("css", "button[type='submit'], #cerca"), ("xpath", "//button[contains(., 'Cerca')]")],
    # Availability list container
    "lista_disponibilita": [("css", "[data-testid*='disponibilita'], .lista-appuntamenti, body")],
    # Single availability row (used by parser)
    "riga_disponibilita": [("css", "tr, [data-testid*='slot'], .slot")],
    # Button to confirm/book chosen slot
    "btn_conferma": [("css", ".conferma, #conferma"), ("xpath", "//button[contains(., 'Conferma')]")],
}

# ------------------------------------------------------------------
# Flow B — Rescheduling (bring forward / postpone) an existing appointment
# ------------------------------------------------------------------
RESCHEDULE = {
    # Menu entry / open "I miei appuntamenti" (Manage Bookings)
    "menu_gestisci": [("css", "a[ng-click*='clickGestisciAppuntamenti']")],
    "menu_gestisci_alt": [("css", "a[ng-click*='clickGestisci']")],
    # Existing appointment card (shows booking code, service, date)
    "card_appuntamento": [("css", "[ng-repeat*='in listaRicetteAppuntamentiCtrl'], .appuntamento, .appuntamento-ssn")],
    # Detail button (opens appointment detail modal)
    "btn_dettaglio": [("css", "button[ng-click*='dettaglioAppuntamento']")],
    # Appointment detail modal: data + actions
    "modal_dettaglio": {
        "btn_riprenota": [("css", "button[ng-click*='riprenota']")],   # Bring forward / Postpone
        "btn_scarica": [("css", "button[ng-click*='AppuntamentoDettaglioModalCtrl.scarica']")],
        "btn_email": [("css", "button[ng-click*='AppuntamentoDettaglioModalCtrl.email']")],
        "btn_chiudi": [("css", "button[ng-click*='AppuntamentoDettaglioModalCtrl.chiudi']")],
    },
    # Warning "not manageable autonomously" (certain appointments)
    "non_gestibile": [("xpath", "//*[contains(., \"Non e' possibile gestire in autonomia\") or contains(., \"Non è possibile gestire in autonomia\")]")],

    # Note: from the 'Anticipa/Posticipa' modal, the flow returns to common
    # booking steps (complete-data modal, Where/When, availability list)
    # mapped in PRENOTAONLINE.
}
