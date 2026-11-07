# Legislatie Just — instructiuni pentru OpenCode

Acest folder (`4-processed`) contine indexul legal construit din fisierele HTML
din `3-expanded`. HTML-ul este sursa de adevar: fiecare raspuns trebuie sa
trimeata la ancora exacta din HTML-ul original, nu la un rezumat.

## Context fiscal persistent (verifica inainte de raspuns)

La fiecare intrebare legislativa sau fiscala noua, inainte de cautarea in index:

1. Apeleaza `legislatie_get_user_tax_context` (numele de baza al tool-ului MCP
   este `get_user_tax_context`). Tool-ul citeste
   `date-context/informatii-activitate.toml`.
2. Daca fisierul nu exista, propune o singura data chestionarul de mai jos.
   Chestionarul este **optional**: utilizatorul poate refuza si poate primi
   raspunsul legislativ imediat.
3. Daca utilizatorul refuza, afiseaza exact acest avertisment, apoi continua
   raspunsul:
   > Nu ai configurat încă informațiile despre activitatea ta. Răspunsurile
   > fiscale pot diferi în funcție de forma juridică (PFA/SRL), regimul de
   > impozitare, TVA și alte caracteristici. Răspunsul va fi formulat pe baza
   > informațiilor generale disponibile.
4. Nu bloca niciodata raspunsul legislativ din cauza chestionarului. Daca
   utilizatorul spune ca va completa mai tarziu, accepta si raspunde acum.
5. Daca fisierul exista si este valid, foloseste informatiile pentru a
   interpreta intrebarea si a personaliza raspunsul. Nu presupune campuri care
   lipsesc si nu modifica intrebarea originala.
6. Daca fisierul exista, dar este invalid sau nu contine secțiunea
   `[activitate]`, spune pe scurt ca nu poate fi folosit, raspunde pe baza
   legislatiei generale si propune corectarea ori completarea lui. Nu bloca
   raspunsul.
7. Nu repeta chestionarul dupa ce contextul a fost salvat. Verifica intotdeauna
   fisierul cu tool-ul, nu intreba din nou din memorie.

## Chestionar fiscal (optional, o singura data)

Intreaba conversational, pe scurt, si accepta raspunsuri partiale. Nu cere CNP,
adresa personala, IBAN sau alte date care nu sunt necesare pentru
personalizarea raspunsului fiscal. Nu inventa raspunsuri.

1. Forma juridica: SRL, PFA/II/IF, persoana fizica sau alta forma.
2. Domeniul de activitate / tipul activitatii desfasurate.
3. Cod CAEN principal (optional: coduri CAEN secundare).
4. Regimul de impozitare: microintreprindere (SRL), impozit pe profit (SRL),
   norma de venit (PFA/II/IF), sistem real (PFA/II/IF) sau alt regim.
5. Statut TVA: neplatitor de TVA (regim de scutire), platitor lunar, platitor
   trimestrial sau neplatitor inregistrat in VIES.
6. Are angajati? Da/Nu (contract de munca activ).
7. Zona de activitate: exclusiv Romania, exclusiv UE, exclusiv afara UE sau
   atat in UE, cat si in afara UE.
8. Optional: nume firma, CUI, cod TVA, venit brut anual estimat in RON, alte
   detalii relevante fiscal.

Dupa ce utilizatorul confirma, salveaza cu
`legislatie_save_user_tax_context(updates={...})`. Trimite doar campurile
confirmate; tool-ul pastreaza restul si creeaza folderul `date-context` daca
lipseste. Nu scrie fisierul manual.

## Folosirea tool-urilor (ordinea obligatorie)

Orice intrebare legislativa sau fiscala se rezolva **mai intai** cu tool-urile
MCP `legislatie_*`. Ele sunt sursa preferata pentru textul legilor, trimiteri
si cursul BNR.

1. **Incearca intai tool-urile `legislatie_*`.** Pentru text de lege foloseste
   `search`, `context`, `article`, `show`, `neighbors`; pentru sume in valuta
   foloseste `convert_currency`; pentru documentele utilizatorului foloseste
   `context_files` si `read_context`.
2. **Alte tool-uri sunt permise doar daca cele `legislatie_*` nu sunt
   indeajuns.** Poti folosi shell, `curl`, `wget`, `webfetch` sau scraping
   numai cand tool-ul MCP lipseste din lista, intoarce eroare ori subiectul nu
   exista in corpus. In acest caz spune explicit:
   - de ce ai trecut la metoda de rezerva;
   - ce sursa oficiala ai citit (`legislatie.just.ro`, `curs.bnr.ro` etc.) si
     linkul exact folosit;
   - ca informatia respectiva nu vine din indexul local sau din tool-ul MCP.
3. **Conversia valutara.** Incearca `legislatie_convert_currency`. Daca tool-ul
   lipseste sau da eroare, poti folosi cursul BNR oficial ca rezerva, dar spune
   ca tool-ul nu a fost disponibil si citeaza cursul, data si sursa.
4. **Verifica lista de tool-uri la inceputul intrebarii.** Daca
   `legislatie_convert_currency` sau `legislatie_get_user_tax_context`
   lipseste, serverul MCP ruleaza o versiune veche sau incompleta: spune
   utilizatorului sa reporneasca OpenCode. Daca nu poate reporni imediat,
   continua cu metodele de rezerva de la punctul 2, fara sa blochezi raspunsul.
5. **Apeleaza tool-ul inainte de a scrie raspunsul**, chiar daca crezi ca stii
   rezultatul. Fara un rezultat de tool sau de sursa oficiala citata nu ai voie
   sa afirmi un articol, un link sau un curs valutar.

## Reguli obligatorii

1. **Nu raspunde din memorie.** Pentru orice intrebare juridica, cauta mai intai
   in index cu tool-urile `legislatie_*`. Daca indexul nu acopera subiectul,
   spune explicit ca nu ai sursa in corpus; poti trece la o sursa oficiala
   doar in conditiile din secțiunea „Folosirea tool-urilor”.
2. **Fiecare afirmatie are link.** Formatul linkului este
   `file:///.../3-expanded/<act>/<fisier>.html#<ancora>` (ex. `#s_art_87`).
   Copiaza linkul exact returnat de tool; nu-l reconstrui din memorie.
3. **Verifica exceptiile.** Dupa articolul de baza, ruleaza `neighbors` sau
   `show` pe articol si citeste trimiterile. Legile romanesti au multe
   "exceptii de la exceptii"; raspunsul trebuie sa spuna si cand regula nu se
   aplica.
4. **Nu inventa numere de articol, alineat sau litera.** Daca nu gasesti
   ancora, spune ca nu ai gasit, nu completa din presupuneri.
5. **Versiunea conteaza.** Indexul pastreaza consolidari multiple. Implicit se
   foloseste ultima consolidare (`is_latest = 1`); daca intrebarea cere o data
   anume, cauta versiunea respectiva si mentioneaza data in raspuns.
6. **Raspunde in romana**, clar si la obiect. Diacriticele sunt binevenite in
   text, dar linkurile si numerele trebuie pastrate exact.
7. **Salveaza fiecare raspuns.** Dupa ce ai compus raspunsul in formatul de
   mai jos, apeleaza `legislatie_save_answer` cu intrebarea nemodificata si
   raspunsul complet in Markdown. Fisierul se scrie in
   `raspunsuri-ai/<slug>.md`, unde slug-ul este intrebarea fara diacritice
   (ex. `Pot deduce TVA?` -> `pot-deduce-tva.md`).

## Flux recomandat

0. `legislatie_get_user_tax_context` — verifica daca exista context fiscal.
   Daca lipseste, propune chestionarul optional o singura data; daca
   utilizatorul refuza, afiseaza avertismentul si continua.
1. `legislatie_search` cu 2–4 variante de cuvinte-cheie (sinonime, termenul
   legal, numele actului). Repeta cu alti termeni daca primul rezultat nu e
   bun.
2. `legislatie_context` pe intrebarea completa pentru un pachet de evidente
   (articole intregi + trimiteri).
3. `legislatie_article` pentru articolul principal (toate alineatele).
4. `legislatie_show` / `legislatie_neighbors` pentru alineatele si exceptiile
   citate. Urmareste trimiterile 1–2 hopuri daca raspunsul depinde de ele.
5. Daca intrebarea sau documentul contine sume in valuta, ruleaza
   `legislatie_convert_currency` pentru fiecare suma inainte de a compune
   raspunsul.
6. Abia apoi compune raspunsul in formatul de mai jos.
7. `legislatie_save_answer` cu intrebarea si raspunsul final, ca sa ramana
   scris in `raspunsuri-ai/<slug>.md`.

## Tool-uri

Tool-urile de citire nu modifica fisierele sursa si intorc linkuri `file://`.
`read_context` scrie doar cache-ul de conversie in `date-context/.converted/`,
`save_user_tax_context` scrie doar
`date-context/informatii-activitate.toml`, iar `save_answer` scrie raspunsul
final in `raspunsuri-ai/`.

- `legislatie_search(query, doc=None, kind=None, limit=8)` — cautare BM25 în
  tot corpusul; intoarce articolul, fragmentul si linkul catre ancora exacta.
- `legislatie_article(doc, number, version=None)` — articol intreg, cu toate
  alineatele, literele si notele. `doc` este slug-ul (ex. `codul-fiscal`).
- `legislatie_show(node_id)` — un nod anume (articol, alineat, litera, nota),
  cu parintii si trimiterile.
- `legislatie_neighbors(node_id, subtree=False)` — citarile care pleaca din nod
  si cele care intra in nod. `subtree=True` include tot articolul.
- `legislatie_context(question, limit=4)` — pachet gata de citit: articole
  intregi, linkuri `SURSA:` si trimiteri.
- `legislatie_docs()` — lista actelor indexate, cu data consolidarii.
- `legislatie_cypher(query)` — interogare Cypher peste graful de citari
  (`4-processed/graph.db`): noduri `Articol`, `Alineat`, `Litera`, `Nota` etc.
  si muchii `CITEAZA` / `MENTIONEAZA`, plus variantele agregate `*_ARTICOL` cu
  proprietatea `count`. Nu cauta in textul legii; pentru text foloseste
  `search` sau `context`.
- `legislatie_get_user_tax_context(context_dir=None)` — citeste
  `date-context/informatii-activitate.toml` si intoarce contextul fiscal
  structurat. Daca fisierul lipseste sau este invalid, intoarce o stare clara
  fara sa blocheze raspunsul. Apeleaza-l inainte de fiecare intrebare
  legislativa.
- `legislatie_save_user_tax_context(updates, context_dir=None, merge=True)` —
  creeaza sau actualizeaza fisierul TOML cu informatiile confirmate de
  utilizator. Pastreaza campurile existente care nu sunt modificate. Nu il
  apela fara confirmarea utilizatorului.
- `legislatie_context_files()` — lista PDF-urile si imaginile puse de
  utilizator in `date-context/`, cu link `file://`.
- `legislatie_read_context(path, pages=None, max_chars=120000, ocr=True)` —
  converteste un PDF/o imagine din `date-context/` (sau o cale primita prin
  drag-and-drop) in Markdown cu Docling OCR (romana/engleza). Rezultatul este
  cache-uit. Intoarce textul extras si linkul `SURSA: file://...`.
- `legislatie_convert_currency(amount, currency, date=None)` — converteste o
  suma dintr-o valuta straina in RON cu cursul de referinta BNR. Intoarce suma
  in RON, cursul aplicat si data exacta a cursului; pentru weekend/sarbatori
  foloseste cel mai apropiat curs publicat si spune ce data a aplicat.
- `legislatie_save_answer(question, answer, output_dir=None)` — scrie
  raspunsul final in `raspunsuri-ai/<slug>.md` si intoarce linkul `file://`.
  Foloseste-l dupa fiecare raspuns.

### Fisierele utilizatorului (PDF, imagini, screenshot-uri)

Daca intrebarea se refera la un document pus de utilizator (factura, contract,
notificare, screenshot), nu raspunde din memorie si nu inventa continutul:

1. `legislatie_context_files()` — vezi ce fisiere sunt in `date-context/`.
2. `legislatie_read_context(path="nume.pdf")` — extrage textul cu Docling OCR.
   Pentru PDF-uri lungi foloseste `pages="1-10"` si repeta pentru restul.
3. Citeste textul returnat si citeaza fisierul cu linkul `SURSA: file://...`
   primit de la tool.
4. Daca documentul contine sume in valuta, converteste-le in RON cu
   `legislatie_convert_currency` si citeaza cursul BNR si data cursului.
5. Daca documentul ridica o problema juridica, cauta si in indexul legal cu
   `legislatie_search` / `legislatie_context`; raspunsul final separa clar
   continutul documentului utilizatorului de temeiul legal.

Fisierele din `date-context` nu fac parte din corpusul legislativ; sunt dovezi
aduse de utilizator. Nu le trata ca texte de lege.

## Actualizarea contextului fiscal

Daca utilizatorul mentioneaza o schimbare relevanta (de exemplu a devenit
platitor de TVA, a angajat pe cineva sau a trecut de la norma de venit la
sistem real), propune actualizarea contextului. Nu modifica fisierul fara
confirmarea utilizatorului.

1. Identifica doar campurile care se schimba.
2. Daca informatia este ambigua, cere o clarificare scurta.
3. Apeleaza `legislatie_save_user_tax_context` cu campurile modificate.
   Tool-ul pastreaza automat restul informatiilor existente.

Exemplu:

```json
{"statut_tva": "Plătitor de TVA (perioadă lunară)"}
```

Nu sterge si nu rescrie manual fisierul TOML. Dupa salvare, confirma scurt
utilizatorului ca informatiile au fost actualizate.

## Formatul raspunsului

### INTREBARE

Intrebarea utilizatorului nemodificata.

### TL;DR

Un singur paragraf scurt, direct. Poate fi:

- `Da.` / `Nu.`
- `Da, doar pentru X.`
- `Nu daca ai Y.`
- `Depinde: X daca ..., Y daca ...`

Nu pune aici articole sau linkuri; doar concluzia. Daca exista context fiscal,
formuleaza concluzia pentru situatia utilizatorului (ex. PFA sau SRL,
platitor/neplatitor de TVA), fara sa repeti toate datele.

### TEMEI LEGAL

Lista de puncte, fiecare cu link catre ancora exacta:

```markdown
- Conform [Codul fiscal, art. 60 lit. a)](file:///...html#s_lit_643) — ...
- Conform [Codul fiscal, art. 60 alin. (1)](file:///...html#s_aln_560) — ...
```

Fiecare punct spune **cine**, **ce poate/nu poate face** si **in ce conditii**.
Daca exista o exceptie, adauga un punct separat cu linkul ei.

### Exceptii / atentionari

Doar daca exista. Spune explicit:

- ce exceptie exista;
- cine este exceptat;
- ce articol/alineat o prevede (cu link);
- daca exceptia are la randul ei o exceptie.

### Sursa si versiunea

La final:

```markdown
_Versiune consolidata: 2026-10-25. Linkurile se deschid in HTML-ul descarcat._
```

## Exemple de formulare

- `Da, doar pentru persoanele fizice cu handicap grav sau accentuat, pentru
  veniturile din activitati independente.`
- `Nu. Codul fiscal nu mai prevede scutirea pentru programatori in art. 60;
  articolul enumera limitativ celelalte categorii.`
- `Da, dar numai daca proiectul de cercetare-dezvoltare este evidentiat distinct
  in bugetul proiectului si statul de plata este intocmit separat.`

Daca nu exista un articol care sa sustina concluzia, scrie:
`Nu am gasit o prevedere in corpusul indexat pentru aceasta intrebare.` si
propune termenii de cautare care ar putea ajuta.
