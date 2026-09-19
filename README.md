# Oneplay pro Home Assistant

[![Ko-fi](https://img.shields.io/badge/Ko--fi-podpo%C5%99%20autora-ff5e5b?logo=ko-fi&logoColor=white)](https://ko-fi.com/matata86)

[![HACS: vlastní repozitář](https://img.shields.io/badge/HACS-vlastn%C3%AD%20repozit%C3%A1%C5%99-41BDF5.svg)](https://hacs.xyz/)

[![Otevřít repozitář v HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=matata86&repository=ha-oneplay&category=integration)
[![Přidat integraci](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=oneplay)

První tlačítko otevře repozitář rovnou v HACS tvojí instance, druhé spustí průvodce nastavením integrace.

Zjistí, co se právě přehrává v aplikaci **Oneplay** — typicky na chytré televizi, kde televizní integrace (např. Samsung, LG, Android TV) sama pozná jen to, že běží appka Oneplay, ale ne jaký pořad. Integrace se přihlásí na tvůj účet Oneplay a čte řadu **„Pokračovat ve sledování“**, jejíž první položka se během přehrávání posouvá.

## Co to umí

- **`sensor.oneplay_prehrava`** — název naposledy sledovaného pořadu, obrázek jako `entity_picture`, a atributy: díl, série, číslo dílu, typ (`episode` / `epgitem`), kategorie, pozice v sekundách, procenta, odkaz na Oneplay.
- **`hraje`** — `true`, pokud se pozice od minulého dotazu posunula (tedy se opravdu přehrává, ne jen pauza na stejném místě).
- **Volitelně vybrané zařízení** (Nastavení integrace → Konfigurovat) — když má účet víc zařízení (TV, tablet, mobil), `hraje` bude platit jen tehdy, když streamuje právě to vybrané. Bez výběru platí pro jakékoli zařízení na účtu.
- **Dotaz jen když je potřeba** — integrace sleduje zadanou entitu televize v HA a na Oneplay se ptá jen tehdy, když má nastavený zdroj Oneplay (jinak by zbytečně zatěžovala účet i síť).

## Instalace

### Přes HACS (vlastní repozitář)

1. HACS → tři tečky vpravo nahoře → **Vlastní repozitáře**.
2. URL: `https://github.com/matata86/ha-oneplay`, kategorie **Integrace**.
3. Najdi **Oneplay** a nainstaluj.
4. Restartuj Home Assistant.

### Ručně

Zkopíruj `custom_components/oneplay` do složky `custom_components` v konfiguraci Home Assistanta a restartuj.

## Nastavení integrace

Nastavení → Zařízení a služby → Přidat integraci → **Oneplay**.

- **E-mail** a **heslo** k účtu Oneplay.
- Pokud má účet víc účtů nebo profilů, integrace nabídne výběr.
- **Televize v Home Assistantu** (`media_player`) a **název jejího zdroje Oneplay** — podle nich integrace pozná, kdy se má vůbec ptát.

Integrace si zařízení na účtu Oneplay pojmenuje „Home Assistant“; při dalším přihlášení starší zařízení se stejným jménem sama odstraní, aby se na účtu nehromadila.

**Nejspolehlivější je mít pro televizi samostatný profil Oneplay** (Možnosti → Profil Oneplay) a v aplikaci na TV používat jen ten: řada „Pokračovat ve sledování“ pak patří jen televizi a rozpoznání pořadu nezpletou ostatní zařízení.

V **Možnostech** integrace (ozubené kolo) jde dodatečně vybrat konkrétní zařízení na účtu Oneplay, které je ta televize — řada „Pokračovat ve sledování“ patří profilu, ne zařízení, takže bez výběru může pozici posouvat i jiné zařízení na stejném profilu (tablet, mobil).

## Jak to funguje uvnitř

Protokol je stejný, jaký používá web Oneplay i [Kodi doplněk `plugin.video.oneplay`](https://github.com/waladir/plugin.video.oneplay): POST na `https://http.cms.jyxo.cz/api/v1.XX/<metoda>` a websocket `wss://ws.cms.jyxo.cz/websocket/<clientId>` pro asynchronní odpovědi (stav `OkAsync`). Integrace čte jen řadu `carousel.display` „Pokračovat ve sledování“ a seznam zařízení (`setting.display`), nic víc.

## Omezení

- API neříká, *které* zařízení konkrétní položku v „Pokračovat ve sledování“ posunulo — jen že se něco posunulo a která zařízení právě streamují. Bez výběru zařízení v nastavení se tedy může stát, že se u vybrané TV zobrazí pořad z jiného zařízení na stejném profilu.
- Živé vysílání (TV kanály) je odzkoušené jen částečně — položky typu `epgitem` se v řadě objevují, ale přesné chování při přepínání kanálů nebylo ověřeno tak důkladně jako u pořadů ze záznamu.
- Při sdíleném profilu integrace odhaduje pořad podle toho, u které dlaždice roste pozice; vybraná dlaždice se drží, dokud roste, a záznam má přednost před živým vysíláním. Jistotu dá jen samostatný profil pro TV.
- Interval dotazu je 60 s, takže změna (pauza/play) se projeví s menším zpožděním než u lokálního přehrávače.

## Řešení potíží

Zapni debug log v `configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.oneplay: debug
```

## Podpora

Pokud ti tahle integrace ušetřila čas: [☕ Ko-fi](https://ko-fi.com/matata86).
