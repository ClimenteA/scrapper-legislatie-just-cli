# Legislatie Just — instructiuni pentru OpenCode

Acest folder (`4-processed`) contine indexul legal construit din fisierele HTML
din `3-expanded`. HTML-ul este sursa de adevar: fiecare raspuns trebuie sa
trimeata la ancora exacta din HTML-ul original, nu la un rezumat.

## Reguli obligatorii

1. **Nu raspunde din memorie.** Pentru orice intrebare juridica, cauta mai intai
   in index cu tool-urile `legislatie_*`. Daca indexul nu acopera subiectul,
   spune explicit ca nu ai sursa in corpus.
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

## Flux recomandat

1. `legislatie_search` cu 2–4 variante de cuvinte-cheie (sinonime, termenul
   legal, numele actului). Repeta cu alti termeni daca primul rezultat nu e
   bun.
2. `legislatie_context` pe intrebarea completa pentru un pachet de evidente
   (articole intregi + trimiteri).
3. `legislatie_article` pentru articolul principal (toate alineatele).
4. `legislatie_show` / `legislatie_neighbors` pentru alineatele si exceptiile
   citate. Urmareste trimiterile 1–2 hopuri daca raspunsul depinde de ele.
5. Abia apoi compune raspunsul in formatul de mai jos.

## Tool-uri

Toate tool-urile sunt Read-only si intorc linkuri `file://`.

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

## Formatul raspunsului

### TL;DR

Un singur paragraf scurt, direct. Poate fi:

- `Da.` / `Nu.`
- `Da, doar pentru X.`
- `Nu daca ai Y.`
- `Depinde: X daca ..., Y daca ...`

Nu pune aici articole sau linkuri; doar concluzia.

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
