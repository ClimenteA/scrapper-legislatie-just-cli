# Format de raspuns (obligatoriu)

Raspunde in romana, cu exact doua secțiuni principale. Nu adauga introduceri
inainte de `INTREBARE`.

## INTREBARE

Intrebarea utilizatorului nemodificata.

## TL;DR

Un singur paragraf, maximum 3 propozitii. Concluzia directa:

- `Da.`
- `Nu.`
- `Da, doar pentru X.`
- `Nu daca ai Y.`
- `Depinde: ...`

Fara linkuri aici.

## TEMEI LEGAL

Fiecare punct: afirmatie + link catre ancora exacta din HTML.

```markdown
- Conform [<Act>, <articol/alineat/litera>](file:///...html#<ancora>) — <ce
  prevede si pentru cine>.
```

Reguli:

- linkul trebuie sa fie cel returnat de tool (nu inventat);
- spune cine poate face ce si in ce conditii;
- daca exista o exceptie, pune un punct separat cu linkul ei;
- daca o exceptie are la randul ei o exceptie, spune-o explicit;
- nu pune mai mult de 6–8 puncte; grupeaza prevederile inrudite;
- daca raspunsul se bazeaza pe un document al utilizatorului (PDF, imagine,
  screenshot), citeste-l intai cu `legislatie_read_context` si citeaza-l cu
  linkul `SURSA: file://...` primit. Separa dovada (documentul) de temeiul
  legal (articolul de lege).

## Exceptii / atentionari

Optional, dar obligatoriu daca exista exceptii. Format:

```markdown
- **Exceptie:** <cine> nu poate <ce>, conform [<articol>](file:///...).
- **Exceptie de la exceptie:** <cine> poate totusi <ce>, conform
  [<articol>](file:///...).
```

## Sursa

```markdown
_Versiune consolidata: <data>. Linkurile se deschid in HTML-ul descarcat._
```

Daca raspunsul foloseste si un fisier din `date-context`, adauga linkul lui
`file://` la final, sub aceeasi linie de sursa.

Daca folosesti contextul fiscal din `date-context/informatii-activitate.toml`,
personalizeaza formularea, dar nu repeta toate datele si nu include CNP, adresa
personala, IBAN sau alte informatii care nu sunt relevante pentru intrebare.

## Salvare

Dupa redactare, salveaza raspunsul in folderul `raspunsuri-ai/`, cu numele
fisierului derivat din intrebare (slug ASCII, fara diacritice):

```bash
uv run scrapper-legislatie-just-cli save-answer \
  "Pot deduce TVA la autoturism?" --answer-file raspuns.md
```

Echivalentul MCP este tool-ul `legislatie_save_answer(question, answer)`.
Fisierul scris este `raspunsuri-ai/pot-deduce-tva-la-autoturism.md`.

## Interzis

- raspunsuri fara link atunci cand exista sursa;
- articole sau alineate inventate;
- amestecarea versiunilor fara sa spui data;
- concluzii bazate doar pe titlul articolului, fara textul alineatului.
