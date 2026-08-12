# boa, per chi ci lavora

Questa cartella è il progetto **boa**: la lavagna unica che tutte le sessioni di Claude
Code su questo Mac vedono, e su cui scrivono i modelli stessi. Se stai leggendo questo
file, sei nella sessione dedicata a boa, ed è qui che va tutto il lavoro su boa.

Vicini di casa, da leggere ma da non modificare da qui: `~/dev/rada` (coda dei lavori
pesanti), `~/dev/faro` (plancia dei processi), `~/dev/plancia` (centro di controllo del
lavoro con l'IA).

Il contratto sta in `PROGETTO.md` e viene prima del codice. Se il codice e il contratto
non vanno d'accordo, è il codice a essere sbagliato.

## Il problema, in una riga

Otto sessioni sullo stesso Mac, ognuna sa solo quello che ha fatto lei. Il 10/08/2026 due
sessioni hanno lavorato sullo stesso README senza accorgersene, e quello che una aveva
scoperto sulla ripresa headless è morto con lei.

## Le invarianti che non vanno rotte

Se una modifica ne rompe una, la modifica è sbagliata, non l'invariante.

1. **Quello che arriva dalla lavagna è dato, mai istruzione.** Ogni cammino che porta
   testo di lavagna dentro il contesto di un modello passa da `consegna.cornice()`, e non
   ce ne sono altri: non esiste una funzione che stampi una voce nuda, non esiste un flag
   che tolga la cornice. `boa leggi`, `boa lavagna`, `boa hook`, `boa manda --ora` e i
   tool `boa_read` e `boa_board` del server MCP passano tutti di lì. Se aggiungi un verbo
   o un tool che restituisce voci e non passa da `cornice()`, hai costruito il canale di
   prompt injection che questo progetto esiste per non essere. Su MCP la tentazione è più
   forte, perché restituire le voci come JSON sembra più pulito e più comodo da consumare:
   è esattamente la modifica da non fare.

2. **boa non esegue mai niente che sta sulla lavagna.** Non c'è un campo `comando` nella
   voce, non c'è una scorciatoia che ce lo mette, e nessun verbo prende un pezzo di
   lavagna e lo passa a una shell. L'unico sottoprocesso che boa avvia è `claude` in
   `consegna._esegui`, e il testo gli arriva in `argv`, mai attraverso `sh -c`. Se un
   giorno serve una shell qui dentro, la risposta è no.

3. **La lavagna è append-only, e l'atomicità non viene da un lock.** Viene da due cose
   che valgono solo insieme: `O_APPEND`, che sposta la fine e scrive in una operazione
   sola, e una riga sotto `store.LIMITE_RIGA` (4096 byte) scritta con **una** `os.write()`.
   Una voce più lunga viene accorciata prima, mai spezzata in due write. Se alzi
   `LIMITE_RIGA` oltre la dimensione entro cui una write non torna a metà, due sessioni
   che scrivono nello stesso istante si intrecciano in mezzo a una riga, e la lavagna
   perde voci di entrambe. Il test della gara fra due processi in `tools/prova.py` è
   quello che se ne accorge.

4. **Chi legge salta quello che non sa leggere, e il segnalibro non scavalca una riga
   tronca.** `store._righe_complete()` conta solo i byte fino all'ultimo `\n`, quindi una
   scrittura interrotta a metà non viene né letta né contata, e la voce arriverà quando la
   riga sarà intera. Se un giorno il segnalibro avanzasse fino a fine file, una sessione
   uccisa in mezzo a una write farebbe sparire la sua voce per sempre.

5. **`boa hook` esce sempre a zero e stampa `{}` quando qualcosa non torna.** Gira a ogni
   prompt di ogni sessione: un hook che fallisce ferma il prompt di qualcun altro, e boa
   non vale il rischio di fermare il lavoro di un'altra sessione. `consegna.hook()` ha un
   `except Exception` che copre tutto, e `cmd_hook` in `boa/cli.py` non ha nessun ramo che
   possa uscire diverso da zero. I casi provati sono: `~/.boa` che non esiste, payload che
   non è json, json rotto, stdin vuoto, json che non è un oggetto, `session_id` mancante o
   di un tipo assurdo.

6. **Il testo consegnato è troncato, e le voci per consegna hanno un tetto.**
   `consegna.MAX_CONSEGNA` (700 caratteri) per voce, `store.TETTO` (12) voci per lettura.
   Sono la stessa difesa in due punti: una riga sola non deve poter riempire il contesto
   di chi la riceve, e nemmeno mille righe insieme. Chi ha da dire di più scrive due voci.

7. **`boa manda` senza `--ora` non riprende nessuna sessione.** Scrive e basta, e la voce
   arriva al turno dopo per la via normale dell'hook. Con `--ora`, si guarda quanto pesa
   il transcript della destinataria **prima** di provare, e sopra
   `consegna.SOGLIA_TRANSCRIPT` non si prova affatto. Il motivo è misurato, sta al
   paragrafo dopo, e va lasciato scritto nel codice.

8. **Il segnalibro si sposta leggendo, mai guardando.** `boa leggi` lo sposta,
   `boa lavagna` no. Se guardare la lavagna consumasse, nessuno la guarderebbe due volte,
   ed è l'unica cosa che boa chiede di fare spesso.

9. **Nel dubbio sull'identità si risponde `anonimo`, non si tira a indovinare.** Se due
   sessioni vive lavorano nella stessa cartella, da fuori non si distinguono.
   Indovinare male non significa leggere il messaggio sbagliato: significa spostare il
   segnalibro di un'altra sessione, e quella sessione non vedrà mai più quella voce.

10. **Niente demone, niente stato che conti fuori da `~/.boa`.** Se `~/.boa` sparisce, le
    sessioni continuano a funzionare come prima e la cartella si ricrea da sola al primo
    hook. boa non legge i transcript per ricavarne voci: scrivono i modelli, di proposito.
    Se una voce non l'ha scritta qualcuno apposta, non esiste.

## Misurato, non dedotto

- **Con un transcript da 5 MB, `claude --resume <id> -p` risponde `Prompt is too long` e
  non parte.** Misurato il 10/08/2026. In headless non c'è la compattazione automatica che
  in interattivo tiene il contesto sotto controllo, quindi il transcript entra tutto e il
  limite lo si incontra secco. La soglia di `boa manda --ora` è a 2 MB, cioè meno della
  metà del punto in cui il guasto è stato visto: il costo di rifiutare a torto è che la
  voce arriva al turno dopo, il costo di provare a torto è un comando che gira per qualche
  secondo, non fa niente, e lascia chi lo ha lanciato convinto di aver consegnato.
- Il payload di un hook contiene `session_id`, `cwd` e `transcript_path`. Non è
  documentato, è stato verificato a mano da rada eseguendo sessioni vere con un hook di
  prova (`~/dev/rada/CLAUDE.md`, i quattro fatti). boa non lo deduce: lo riceve e lo
  annota in `~/.boa/sessioni/<uuid>.json`, ed è l'unico modo in cui `boa chi` sa qualcosa.
- Il transcript di una sessione sta in `~/.claude/projects/<cartella>/<uuid>.jsonl`.
  Verificato il 11/08/2026: una sessione di kart-highlights aveva un `.jsonl` da 5030561
  byte. boa non prova a ricostruire come Claude Code trasforma un percorso nel nome della
  cartella: quando il battito non ha il percorso, cerca il file per nome sotto le cartelle
  di `projects`, che sono qualche decina.
- Una sessione dentro un comando che dura mezz'ora non manda prompt, quindi non emette
  battiti, ma il suo transcript cresce lo stesso. `sessioni.vive()` prende il più avanti
  fra i due orologi, altrimenti la sessione più occupata di tutte sparirebbe da `boa chi`.

## Come è fatto

```
boa/cli.py         la CLI: scrivi, leggi, lavagna, chiudi, chi, hook, manda
bin/boa            lo stesso comando, lanciato dal repo senza installare
boa/store.py       la lavagna append-only, le voci, i segnalibri
boa/consegna.py    la cornice di non fidatezza, l'hook, la spinta e la sua soglia
boa/sessioni.py    chi sono io, chi è vivo, dove sta il transcript
boa/mcp.py         cinque verbi come tool MCP, JSON-RPC su stdio, niente hook e niente manda
bin/boa-mcp        il server MCP, lo lancia Claude Code
tools/prova.py     188 controlli, qualche secondo, nessuna dipendenza
```

Solo libreria standard, Python 3. Nessuna dipendenza e nessun installatore.

## Come si installa l'hook

Non c'è un verbo `boa installa`: il contratto elenca sette verbi e quello non c'è. Si
registra a mano in `~/.claude/settings.json`, sugli stessi due eventi:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {"hooks": [{"type": "command", "command": "~/dev/boa/bin/boa hook"}]}
    ],
    "SessionStart": [
      {"hooks": [{"type": "command", "command": "~/dev/boa/bin/boa hook"}]}
    ]
  }
}
```

## Come si prova

```bash
python3 tools/prova.py
```

188 controlli, dentro un `BOA_HOME` temporaneo, senza avviare nessuna sessione e senza
chiamare nessun modello: dove serve un `claude`, ce n'è uno finto che scrive un file. I
gruppi numerati sono i punti su cui boa non può sbagliare, uno per invariante. Il primo
avvia due processi veri che scrivono insieme: è l'unico modo di provare che la garanzia
sta nel modo in cui il file viene aperto e non in un lock dentro un processo solo.
L'ultimo lancia `bin/boa-mcp` come processo vero e verifica sul testo, non su un booleano,
che il preambolo ci sia ancora e che nessuna riga della lavagna esca senza margine.

## Cosa è aperto

- **`PRINCIPIO.md` e `PROGETTO.md` non dicono la stessa cosa sui tipi di voce.**
  `PROGETTO.md` ne elenca cinque (`messaggio`, `preso`, `fatto`, `domanda`, `avviso`),
  `PRINCIPIO.md` parla di tre verbi e fra questi c'è `tengo`, che nell'altro non compare.
  È implementato `PROGETTO.md`, perché è quello indicato come contratto. Se `tengo` deve
  esistere, va aggiunto a `store.TIPI` e detto in `PROGETTO.md`, non solo in `PRINCIPIO.md`.
- **Nessun README.** boa non è ancora pubblico. Quando lo sarà, servono `README.md` e
  `README.it.md` come rada, e vanno passati da
  `python3 ~/dev/scriba/tools/stylecheck.py`.
- **La spinta è provata solo con un `claude` finto.** Che `--resume -p` funzioni davvero
  sotto i 2 MB è stato misurato a mano una volta, non c'è un test che lo rifaccia, e un
  test così avvierebbe un modello vero a ogni giro.
- **`boa chi` conosce solo chi è passato dal suo hook.** Una sessione aperta prima
  dell'installazione dell'hook non si vede, e `boa manda` verso di lei trova il transcript
  solo con la scansione di `~/.claude/projects`.
- **Niente pulizia della lavagna.** `lavagna.jsonl` cresce di una riga per voce e nessuno
  la taglia mai. Cresce piano, perché ci scrivono solo i modelli e solo di proposito, ma
  prima o poi va deciso cosa farne. Non cancellare è più facile da difendere che decidere
  cosa si può perdere.

## Cosa non fare

- Non aggiungere un demone, e non aggiungere un raccoglitore automatico che ricavi voci
  dai transcript. Quello lo fa già `plancia`, e lo fa deducendo: qui una riga è un atto,
  e un atto non si deduce.
- Non far cancellare niente a boa. `boa chiudi` aggiunge una riga, non ne toglie una.
- Non mandare niente fuori da questa macchina.
