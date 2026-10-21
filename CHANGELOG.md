## v0.1.0 (2026-10-21)

### Feat

- **portable**: add interactive setup wizard, PyInstaller spec, and GitLab/GitHub CI/CD pipelines
- send debug screenshots via Telegram and add multi-province search strategies
- **debug**: add customizable debug dump directory with screenshot references on error
- **auth**: implement interactive multi-level CIE login flow with Telegram OTP and QR fallback
- **blacklist**: add blacklist management for rejected proposals with telegram commands and expiration tracking
- **bot**: add /pause and /resume commands to toggle and control polling
- **auth**: implement interactive fallback and OTP callback for SPID SielteID
- **docker**: add Portainer-compatible stack and deployment guide
- **flow**: clarify post-booking monitoring option as anticipo search
- **auth**: add inspection study, selectors and auth providers for CIE and major SPID providers
- **flow**: prompt user post-booking to stop or continue monitoring as reschedule
- **auth**: add configurable authentication provider architecture for SPID and CIE
- **telegram**: auto-register bot commands via set_my_commands and run startup session concurrently
- **docker**: add Xvfb virtual display support and container entrypoint
- **booking**: proposta Maps, attesa loading lunga e scadenza proposta su TG
- monitor appuntamenti fascicolo sanitario regione lombardia

### Fix

- **wizard**: harden input validation, handle KeyboardInterrupt/EOFError, and normalize dynamic base paths
- **browser**: add resilient Chrome/Chromium auto-discovery, invalid binary recovery, and clear diagnostics
- **core**: risoluzione criticità ciclo di vita monitor, anticipo orario e province
- **flows**: safe card matching in RescheduleFlow and NRE normalization in NewBookingFlow
- **scheduler**: automatically reset polling pause on monitor creation or complete removal
- **reschedule**: prevent polling skip and re-arm monitors in done state
- **flows**: verify booking outcome and confirm presence in 'I miei appuntamenti'
- **ricette**: prioritize bookable recipes over administrative accepted status
- **reschedule**: dynamic configurable timeout for loading 'I miei appuntamenti'
- risolto blocco e mancato re-login con /status e /poll
- **session**: keep browser alive after automated login and prevent loop
- **telegram**: add dedicated error handler for HTTP 409 Conflict diagnostics
- **core**: fix dead browser recovery on wake-up, thread locking, and push retry timeout
- resolve invalid css selectors, redundant timeouts, and reschedule conversion bugs
- **scheduler**: add post-navigation session verification to get_appuntamenti and get_ricette
- **docker**: clean stale Xvfb lock files on container restart
- **session**: ensure expired sessions trigger re-login and prevent false empty availability
- **core**: resolve edge cases in matcher, calendar, store, ricette and telegram bot
- **session**: handle login failures and notify on expired session in bot commands
- **new_booking**: chiudi le tab residue a ogni ciclo (RAM)
- **browser**: stato 'risultati' prima dello spinner in _stato_vista
- **monitoring**: nre corretto da parse_ricette + form atteso prima di compilare
- **session**: relogin robusto sotto nohup + riavvio driver se cambia headless
- add maps link for proposed date
- **booking**: provincia corretta negli slot, parser appuntamenti + notifiche /poll
