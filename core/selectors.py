"""MAPPA SELEZIONI DOM per portale Fascicolo Sanitario Regione Lombardia.

Questo è l'unico file da compilare durante la sessione di studio con Selenium.
Ogni selettore è una coppia (strategia, valore). Le strategie valide:
    "id", "name", "css", "xpath", "class"

Convenzione: un selettore può essere stringa (unico) oppure list di stringhe
(alternative provate in ordine) per robustezza.
"""
from __future__ import annotations

# ------------------------------------------------------------------
# Login SPID — IdPC Regione Lombardia (/PublisherMetadata/SSOService)
# ------------------------------------------------------------------
LOGIN_SPID = {
    "url_accedi": "https://www.fascicolosanitario.regione.lombardia.it/web/areaprivata/homepage",
    "url_idpc": "https://idpcwrapper.crs.lombardia.it/PublisherMetadata/SSOService",
    # form che esegue submit verso l'IdP scelto
    "form_spid": [("id", "SPIDform")],
    "field_indexIdp": [("id", "indexIdp")],
    # lista IdP verificata su IdPC Regione Lombardia
    "idp": {
        "Infocert": 0, "Register": 1, "Lepida": 2, "IntesiGroupSpid": 3,
        "Aruba": 4, "Namirial": 5, "Poste": 6, "InfoCamere": 7,
        "Tim": 8, "Sielte": 9, "TeamSystemId": 10, "EtnaID": 11,
    },
    # selettore dei box provider (il click imposta indexIdp e fa submit)
    "box_idp": [("css", "a.home-box-fornitore")],
}

# ------------------------------------------------------------------
# Login CIE — Carta d'Identità Elettronica (Ministero dell'Interno)
# ------------------------------------------------------------------
LOGIN_CIE = {
    "url_landing": "https://idserver.servizicie.interno.gov.it/idp/login/livello2",
    "btn_entra_cie": [
        ("css", "form[action*='AuthRequestCieService']"),
        ("css", ".pulsante-cie form"),
        ("css", ".pulsante-cie"),
        ("css", "[cie-button]"),
    ],
    "form": [("id", "loginUP")],
    "username": [("id", "username"), ("name", "username")],
    "password": [("id", "password"), ("name", "password")],
    "btn_prosegui": [("css", "form#loginUP button[type='submit']"), ("css", "button[type='submit']")],
    "link_app_cie": [("css", "a[href*='login/app']")],
    "consenso": [
        ("css", "button[name='confirm']"),
        ("css", "button[type='submit']"),
        ("xpath", "//button[contains(., 'Autorizza') or contains(., 'Acconsento') or contains(., 'Prosegui')]"),
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
# username = Codice Fiscale; dopo il submit l'IdP chiede conferma via app/OTP.
# ------------------------------------------------------------------
LOGIN_IDP_SIELTEID = {
    "url_matcher": "identity.sieltecloud.it",
    "username": [("id", "username")],
    "password": [("id", "password")],
    "form": [("id", "piLoginForm")],
    "btn_prosegui": [("id", "autorizza")],
    "field_authstate": [("name", "AuthState")],
    "scelta_metodo": {
        "url_matcher": "Scegli il metodo",
        "notifica": [("eval", "useNotify()")],
        "otp_app": [("eval", "useAPP()")],
        "field_usenotify": [("id", "usenotify")],
        "field_useapp": [("id", "useapp")],
    },
    "consenso": {
        "url_matcher": "Autorizza",
        "btn_autorizza": [("css", "form#piLoginForm button[type='submit']")],
        "field_accept": [("id", "accept")],
    },
    "post_login_url": "https://www.fascicolosanitario.regione.lombardia.it/web/areaprivata/ricette",
}

# ------------------------------------------------------------------
# Pagina Ricette (/web/areaprivata/ricette) — elenco ricette prenotabili
# ------------------------------------------------------------------
RICETTE = {
    "url": "https://www.fascicolosanitario.regione.lombardia.it/web/areaprivata/ricette",
    # checkbox di filtro
    "filtro_specialistiche": [("id", "specialisticheID")],
    "filtro_prescritte": [("id", "prescritteID")],
    # ogni card ricetta "Prenota" porta a prenotaonline con token hash
    "card_prenota": [("css", "a.cambia-visibilita[href*='prenotaonline']")],
}

# ------------------------------------------------------------------
# App prenotaonline — SPA (da studiare: menu area + lista disponibilità)
# ------------------------------------------------------------------
PRENOTAONLINE = {
    "url": "https://www.fascicolosanitario.regione.lombardia.it/prenotaonline/le-tue-ricette",
    # --- step 1: scelta ricetta -> click su "Prenota" della ricetta specialistica (visita) ---
    "btn_prenota_ricetta": [("css", "button[ng-click*='prenotaAppuntamento']")],
    # --- modale "Completa dati" (CONTROLLO/FOLLOW-UP?) : radio Si/No + Conferma ---
    "modal_completa_dati": {
        "radio_si": [("css", "input[name=controllo][value=true]")],
        "radio_no": [("css", "input[name=controllo][value=false]")],
        # NB: il controller reale è prenotaCompletaDatiCtrl (C maiuscola)
        "btn_conferma": [("css", "button[ng-click*='CompletaDatiCtrl.conferma']")],
    },
    # --- modale informativa da chiudere ---
    "modal_info_chiudi": [("css", "button[ng-click*='messaggiCtrl.close']")],
    # --- step 2: Dove/Quando ---
    "sel_provincia": [("id", "provincia")],
    # province disponibili nel dropdown (da proporre all'utente su Telegram)
    "province": [
        "BERGAMO", "BRESCIA", "COMO", "CREMONA", "LECCO", "LODI",
        "MANTOVA", "MILANO CITTA'", "MILANO PROVINCIA", "MONZA E DELLA BRIANZA",
        "PAVIA", "SONDRIO", "VARESE",
    ],
    "input_data_dal": [("id", "quando")],
    "input_telefono": [("id", "telefono")],
    "input_email": [("id", "email")],
    "checkbox_consenso": [("id", "consensoPrenotazione")],
    # bottone Conferma dopo i recapiti che porta alla lista disponibilità
    "btn_conferma_dove_quando": [("css", "button[ng-click*='doveQuandoCtrl.conferma'], button:has-text('Conferma')")],
    # bottone che invia la ricerca dopo Dove/Quando (abilita solo se form VALID)
    "btn_ricerca_disponibilita": [("css", "button[ng-click*='doveQuandoCtrl.ricercaDisponibilita']")],
    # --- step 3: LISTA DISPONIBILITA' ---
    # alcuni pulsanti compaiono sia nella vista risultati che nella modale di modifica
    "btn_modifica_ricerca": [("css", "button[ng-click*='prenotaDisponibilitaCtrl.modificaCriteri']")],  # apre la modale
    "btn_aggiorna_ricerca": [("css", "button[ng-click*='doveQuandoModalCtrl.aggiorna']")],          # nella modale, rilancia la ricerca
    "btn_chiudi_modale_dove": [("css", "button[ng-click*='doveQuandoModalCtrl.chiudi']")],
    # --- modale "Modifica ricerca": criteri configurabili (utilizzati dal bot) ---
    "giorni_settimana": {
        "tutti": [("id", "tutti")],
        "lun": [("id", "lun")], "mar": [("id", "mar")], "mer": [("id", "mer")],
        "gio": [("id", "gio")], "ven": [("id", "ven")], "sab": [("id", "sab")],
        "dom": [("id", "dom")],
        # ng-model usato da Angular (per binding corretto)
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
    # blocco di un singolo slot (data/ora + azienda + sede); i campi sono ger archi
    "slot_disponibilita": [("css", "[ng-repeat*='disponibilita in disponibilitaCtrl.valori']")],
    # campi dentro ogni slot
    "slot_data_ora": [("css", "span[ng-if*='isClassico']")],              # 'DD/MM/YYYY - HH:MM'
    "slot_azienda": [("css", "[ng-repeat*='disponibilita in disponibilitaCtrl.valori'] .appuntamento-field-value span")],
    "slot_none_msg": [("css", "h5:has-text('Non sono state trovate disponibilità')")],
    # pulsante "Verifica e conferma" per uno slot
    "btn_verifica_conferma": [("css", "button[ng-click*='setAzioneRichiestaConfermaDisponibilita']")],
    # --- modale conferma prenotazione (dopo 'Verifica e conferma') ---
    "modal_conferma_prenota": {
        "checkbox_lettura": [("id", "presaVisioneNote")],
        "btn_conferma": [("css", "button[ng-click*='verificaPrenotazioneCtrl.conferma']")],
        "btn_annulla": [("css", "button[ng-click*='verificaPrenotazioneCtrl.annulla']")],
    },
    # schermata di successo prenotazione
    "esito_successo": [("css", "*:has-text('Prenotazione effettuata con successo')")],
    "esito_codice": [("css", "*:has-text('Codice prenotazione')")],
    # area appuntamento confermato (per annullo/aggiornamento)
    "btn_annulla_appuntamento": [("css", "button:has-text('Annulla appuntamento')")],
    # pulsanti per ridurre/cercare altre date/orari
    "btn_ricerca_altre_date": [("css", "button[ng-click*='ricercaUlterioriDisponibilita'][ng-click*=\"'D'\"]")],
    "btn_ricerca_altri_orari": [("css", "button[ng-click*='ricercaUlterioriDisponibilita'][ng-click*=\"'O'\"]")],
}

# ------------------------------------------------------------------
# Sezione agende appuntamenti
# ------------------------------------------------------------------
BOOKING = {
    # voce di menu / link per aprire le prenotazioni
    "apri_prenotazioni": [("css", "a[href*='prenotazioni'], a[aria-label*='prenotazione']")],
    # per Flow A: campo/elenco ricetta da selezionare
    "sel_ricetta": [("css", "select[name*='ricetta'], #ricetta, [data-testid*='ricetta']")],
    # menu a tendina dell'area/specialità
    "sel_area": [("css", "select[name*='area'], #area, [data-testid*='area']")],
    # pulsante che carica la pagina delle disponibilità
    "btn_cerca": [("css", "button[type='submit'], button:has-text('Cerca'), #cerca")],
    # contenitore della lista delle disponibilità (righe = slot)
    "lista_disponibilita": [("css", "[data-testid*='disponibilita'], .lista-appuntamenti, body")],
    # singola riga di disponibilità (usato dal parser)
    "riga_disponibilita": [("css", "tr, [data-testid*='slot'], .slot")],
    # pulsante per confermare/prenotare lo slot scelto
    "btn_conferma": [("css", "button:has-text('Conferma'), .conferma, #conferma")],
}

# ------------------------------------------------------------------
# Flow B — riprogrammazione (anticipa/posticipa) di una visita già prenotata
# ------------------------------------------------------------------
RESCHEDULE = {
    # voce di menu / apertura "I miei appuntamenti" (Gestisci Prenotazioni)
    "menu_gestisci": [("css", "a[ng-click*='clickGestisciAppuntamenti']")],
    "menu_gestisci_alt": [("css", "a[ng-click*='clickGestisci']")],
    # card dell'appuntamento esistente (mostra cod. prenotazione, prestazione, data)
    "card_appuntamento": [("css", "[ng-repeat*='in listaRicetteAppuntamentiCtrl'], .appuntamento, .appuntamento-ssn")],
    # bottone Dettaglio (apre la modale Dettaglio appuntamento)
    "btn_dettaglio": [("css", "button[ng-click*='dettaglioAppuntamento']")],
    # modale Dettaglio appuntamento: dati + azioni
    "modal_dettaglio": {
        "btn_riprenota": [("css", "button[ng-click*='riprenota']")],   # Anticipa/Posticipa
        "btn_scarica": [("css", "button[ng-click*='AppuntamentoDettaglioModalCtrl.scarica']")],
        "btn_email": [("css", "button[ng-click*='AppuntamentoDettaglioModalCtrl.email']")],
        "btn_chiudi": [("css", "button[ng-click*='AppuntamentoDettaglioModalCtrl.chiudi']")],
    },
    # avviso "non gestibile in autonomia" (alcuni appuntamenti)
    "non_gestibile": [("css", "*:has-text('Non e\\' possibile gestire in autonomia')")],

    # NB: dalla modale 'Anticipa/Posticipa' si rientra negli step comuni del
    # flusso di prenotazione (modale completa-dati, Dove/Quando, lista
    # disponibilità) mappati in PRENOTAONLINE. I selettori sono riusabili.
}
