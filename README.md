# Biedronka (unofficial) — Home Assistant

[![CI](https://github.com/Przemko92/home-assistant-mojabiedronka/actions/workflows/ci.yml/badge.svg)](https://github.com/Przemko92/home-assistant-mojabiedronka/actions/workflows/ci.yml)
[![hacs][hacsbadge]][hacs]
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-ffdd00?logo=buy-me-a-coffee&logoColor=black)](https://buymeacoffee.com/przemko92)

Unofficial [HACS](https://hacs.xyz) integration for the **Moja Biedronka** app: purchase history and Shakeomat.

Nieoficjalna integracja [HACS](https://hacs.xyz) do programu **Moja Biedronka**: historia transakcji i Shakeomat.

**Not affiliated with Jeronimo Martins Polska S.A.** · **Nie jest powiązana z Jeronimo Martins Polska S.A.**

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Przemko92&repository=home-assistant-mojabiedronka&category=integration)
[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=biedronka)

**Required add-on / wymagany dodatek:** [Browser Companion](https://github.com/Przemko92/homeassistant-browser-companion) (`https://github.com/Przemko92/homeassistant-browser-companion`)

> 🇬🇧 English below · 🇵🇱 Polski poniżej

---

## 🇬🇧 English

Unofficial integration for **Moja Biedronka**. It uses the private API of the mobile app. The terms of service do not cover this — use at your own risk (API changes, account, SMS limits).

Repository: [github.com/Przemko92/home-assistant-mojabiedronka](https://github.com/Przemko92/home-assistant-mojabiedronka)

**Required:** [Browser Companion](https://github.com/Przemko92/homeassistant-browser-companion) add-on — without it this integration cannot sign in.

### Installation (HACS)

1. Install the **[Browser Companion](https://github.com/Przemko92/homeassistant-browser-companion)** add-on (Home Assistant OS or Supervised only):
   - Settings → Add-ons → Add-on Store → ⋮ → **Repositories**
   - Add `https://github.com/Przemko92/homeassistant-browser-companion`
   - Install **Browser Companion**, start it, and confirm Chromium appears in the sidebar
2. HACS → ⋮ → **Custom repositories**
3. URL: `https://github.com/Przemko92/home-assistant-mojabiedronka`, category **Integration**
4. Search **Biedronka** → Download → restart Home Assistant
5. Settings → Devices & services → Add integration → **Biedronka**

You can also use the [My Home Assistant](https://my.home-assistant.io/redirect/hacs_repository/?owner=Przemko92&repository=home-assistant-mojabiedronka&category=integration) button above.

Manual: copy `custom_components/biedronka/` to `/config/custom_components/` and restart. The [Browser Companion](https://github.com/Przemko92/homeassistant-browser-companion) add-on is still required.

### Sign-in

The only method is the **[Browser Companion](https://github.com/Przemko92/homeassistant-browser-companion)** add-on on **Home Assistant OS** or **Supervised**. The integration opens a Chromium session and waits for **HTTP 302** with `Location: app://cma20.biedronka.pl`. You complete captcha and SMS in the sidebar browser.

Without Supervisor (e.g. Container / Core) setup will fail — Companion cannot be installed there.

The official app **never shows** `refresh_token` in settings — that is expected. SMS limit: about 10 attempts, then a lockout of ~30 minutes.

### Entities

| Entity | Description |
| --- | --- |
| `sensor.biedronka_*_karta` / Loyalty card | Card number |
| Last transaction | Amount in PLN, store, receipt, line items (EAN) |
| Recent receipts | Up to 5 recent receipts in the `receipts` attribute (id, date, store, number, total, source, and slim fiscal lines). Fetched on startup and on refresh |
| Today's spend | Sum from today's receipts |
| Shakeomat | Nearest offer: `available` / `cooldown` / `expired` / `claimed` / `none`; full list in the `offers` attribute |
| Shakeomats ready | How many offers can be claimed right now |
| Last Shakeomat offer | Name of the last claimed reward; previous ones in the `history` attribute |
| Reveal Shakeomats | `PATCH …/offers/{id}/reveal-and-activate/` for each available offer |
| Auto Shakeomat | Off by default — when enabled, uses the daily limit the same way as the phone |

There are no separate “Shakeomat 1” and “Shakeomat 2” entities. The API can return further offers under the same `SHAKEOMAT` type, and besides the two daily ones there are sometimes extras (including `SHAKEOMARKA`), so the number of offers varies and the integration handles any count. Old slot-based entities are removed from the registry on startup.

Claimed offers are stored in integration storage and survive Home Assistant restarts — a new offer does not wipe the previous one. After each activation a separate `persistent_notification` appears with the offer name.

Refresh interval: every 15 minutes (integration options: 5–120 min).

### Out of scope for v0.1

BLIK, complaints, leaflets, stock levels, household, surveys.

### Development / debug

You can test the integration itself (without the add-on) in this repo: Dev Containers → HA on port 8123. **There is no Supervisor** — Browser Companion will not install here.

Full flow (Supervisor + Ingress + add-on + Biedronka): open **homeassistant-browser-companion** in a Supervisor devcontainer. See `homeassistant-browser-companion/.devcontainer/README.md`. Keep the repositories next to each other (`GIT/moja-biedronka` and `GIT/homeassistant-browser-companion`).

Details for local HA without the add-on: [`.devcontainer/README.md`](.devcontainer/README.md). `debugpy.wait: true` pauses HA startup until you attach the debugger.

Locally without a container: `scripts/setup` then `scripts/develop`. The `config/` directory is in `.gitignore`.

### API (app 2.22.2)

- `https://api.prod.biedronka.cloud/api/v7/`
- `User-Agent: Android/2.22.2`
- Token: `https://konto.biedronka.pl/realms/loyalty/protocol/openid-connect/token`

### Support

If this integration helps you, you can buy a coffee: [Buy Me a Coffee](https://buymeacoffee.com/przemko92)

---

## 🇵🇱 Polski

Nieoficjalna integracja do programu **Moja Biedronka**. Korzysta z prywatnego API aplikacji mobilnej. Regulamin tego nie przewiduje — używasz na własne ryzyko (zmiany API, konto, limity SMS).

Repozytorium: [github.com/Przemko92/home-assistant-mojabiedronka](https://github.com/Przemko92/home-assistant-mojabiedronka)

**Wymagane:** dodatek [Browser Companion](https://github.com/Przemko92/homeassistant-browser-companion) — bez niego integracja nie zaloguje się.

### Instalacja (HACS)

1. Zainstaluj dodatek **[Browser Companion](https://github.com/Przemko92/homeassistant-browser-companion)** (tylko Home Assistant OS albo Supervised):
   - Ustawienia → Dodatki → Sklep z dodatkami → ⋮ → **Repositories**
   - Dodaj `https://github.com/Przemko92/homeassistant-browser-companion`
   - Zainstaluj **Browser Companion**, uruchom go i sprawdź, czy Chromium pojawia się w sidebarze
2. HACS → ⋮ → **Custom repositories**
3. URL: `https://github.com/Przemko92/home-assistant-mojabiedronka`, kategoria **Integration**
4. Szukaj **Biedronka** → Download → restart Home Assistant
5. Ustawienia → Urządzenia i usługi → Dodaj integrację → **Biedronka**

Możesz też użyć przycisku [My Home Assistant](https://my.home-assistant.io/redirect/hacs_repository/?owner=Przemko92&repository=home-assistant-mojabiedronka&category=integration) powyżej.

Ręcznie: skopiuj `custom_components/biedronka/` do `/config/custom_components/` i zrestartuj. Dodatek [Browser Companion](https://github.com/Przemko92/homeassistant-browser-companion) jest nadal wymagany.

### Logowanie

Jedyna metoda: dodatek **[Browser Companion](https://github.com/Przemko92/homeassistant-browser-companion)** na **Home Assistant OS** albo **Supervised**. Integracja otwiera sesję Chromium i czeka na **HTTP 302** z `Location: app://cma20.biedronka.pl`. Captcha i SMS robisz w przeglądarce w sidebarze.

Bez Supervisora (np. Container / Core) konfiguracja się nie uda — Companion nie da się tam zainstalować.

Oficjalna apka **nigdy nie pokazuje** `refresh_token` w ustawieniach — to normalne. Limit SMS: ok. 10 prób, potem blokada na ~30 minut.

### Encje

| Encja | Opis |
| --- | --- |
| `sensor.biedronka_*_karta` / Loyalty card | Numer karty |
| Ostatnia transakcja | Kwota PLN, sklep, paragon, pozycje (EAN) |
| Ostatnie paragony | Do 5 ostatnich paragonów w atrybucie `receipts` (id, data, sklep, numer, suma, źródło i okrojone linie fiskalne). Pobierane przy starcie i przy odświeżeniu |
| Dzisiejsze wydatki | Suma z dzisiejszych paragonów |
| Shakeomat | Najbliższa oferta: `available` / `cooldown` / `expired` / `claimed` / `none`, pełna lista w atrybucie `offers` |
| Shakeomaty do odebrania | Ile ofert można odebrać w tej chwili |
| Ostatnia oferta Shakeomatu | Nazwa ostatnio odebranej nagrody, wcześniejsze w atrybucie `history` |
| Odbierz Shakeomaty | `PATCH …/offers/{id}/reveal-and-activate/` dla każdej dostępnej oferty |
| Auto Shakeomat | Domyślnie wyłączony — po włączeniu zużywa dzienny limit tak samo jak telefon |

Nie ma osobnych encji „Shakeomat 1” i „Shakeomat 2”. API potrafi podać kolejne oferty pod tym samym typem `SHAKEOMAT`, a poza dwiema dobowymi bywają dodatkowe (również `SHAKEOMARKA`), więc liczba ofert jest zmienna i integracja obsługuje ich dowolnie wiele. Stare encje ze slotami są usuwane z rejestru przy starcie.

Odebrane oferty trafiają do magazynu integracji i przeżywają restart Home Assistanta — nowa oferta nie kasuje poprzedniej. Po każdej aktywacji pojawia się osobny `persistent_notification` z nazwą oferty.

Odświeżanie: co 15 minut (opcje integracji: 5–120 min).

### Poza zakresem v0.1

BLIK, reklamacje, gazetki, stany magazynowe, gospodarstwo domowe, ankiety.

### Development / debug

Samą integrację (bez add-onu) testujesz w tym repo: Dev Containers → HA na porcie 8123. **Nie ma Supervisora** — Browser Companion się tu nie zainstaluje.

Pełny flow (Supervisor + Ingress + dodatek + Biedronka): otwórz **homeassistant-browser-companion** w devcontainerze z Supervisorem. Opis: `homeassistant-browser-companion/.devcontainer/README.md`. Repozytoria trzymaj obok siebie (`GIT/moja-biedronka` i `GIT/homeassistant-browser-companion`).

Szczegóły lokalnego HA bez add-onu: [`.devcontainer/README.md`](.devcontainer/README.md). `debugpy.wait: true` wstrzymuje start HA, aż podepniesz debugger.

Lokalnie bez kontenera: `scripts/setup` potem `scripts/develop`. Katalog `config/` jest w `.gitignore`.

### API (aplikacja 2.22.2)

- `https://api.prod.biedronka.cloud/api/v7/`
- `User-Agent: Android/2.22.2`
- Token: `https://konto.biedronka.pl/realms/loyalty/protocol/openid-connect/token`

### Wsparcie

Jeśli integracja Ci pomaga, możesz postawić kawę: [Buy Me a Coffee](https://buymeacoffee.com/przemko92)

***

[hacs]: https://github.com/hacs/integration
[hacsbadge]: https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge
