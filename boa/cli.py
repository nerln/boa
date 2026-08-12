"""boa: la lavagna che tutte le sessioni vedono.

  boa scrivi "testo"                    a tutti, tipo messaggio
  boa scrivi --a progetto:faro --tipo preso "rifaccio io il README"
  boa leggi                             cosa c'e' per me che non ho gia' visto
  boa lavagna                           tutto quello che e' aperto, di tutti
  boa lavagna --progetto faro           solo un progetto
  boa chiudi <id> "com'e' andata"
  boa chi                               quali sessioni sono vive adesso
  boa hook                              per gli hook di Claude Code, legge stdin
  boa manda <sessione> "prompt"         scrive; spinge solo con --ora

  --io <uuid>   dichiara chi sei, se boa non riesce a capirlo da solo

`boa leggi` sposta il segnalibro, `boa lavagna` no: guardare la lavagna non deve far
sparire niente. Un `boa` senza verbo stampa questo aiuto e non legge niente, perche' un
comando battuto per sbaglio non deve consumare messaggi.
"""
import argparse
import os
import sys

from boa import __version__, consegna, sessioni, store


def _identita(args):
    return sessioni.identita(getattr(args, "io", None))


def _stampa(testo):
    if testo:
        print(testo)


# --------------------------------------------------------------------------- verbi

def cmd_scrivi(args):
    mia = _identita(args)
    try:
        rec = store.scrivi(mia, a=args.a, tipo=args.tipo, testo=args.testo,
                           riferimento=args.riferimento,
                           una_volta=getattr(args, "una_volta", False),
                           chiave=getattr(args, "chiave", None),
                           ambito=getattr(args, "ambito", "sessione"))
    except ValueError as e:
        print(f"boa: {e}", file=sys.stderr)
        return 2
    if rec is None:
        # Non e' un errore: e' la lavagna che rifiuta di ripetersi. Chi chiama
        # capisce dallo stdout vuoto che non ha detto niente di nuovo, e puo'
        # tacere anche lui invece di notificare due volte la stessa cosa.
        return 0
    print(f"{rec['id']}  {rec['tipo']}  a {rec['a']}")
    return 0


def cmd_leggi(args):
    mia = _identita(args)
    voci = store.nuove(mia["sessione"], mia["progetto"], sposta=True)
    if not voci:
        print("niente di nuovo per te.")
        return 0
    _stampa(consegna.cornice(voci))
    return 0


def cmd_lavagna(args):
    testo = consegna.lavagna(args.progetto)
    if not testo:
        dove = f" per {args.progetto}" if args.progetto else ""
        print(f"la lavagna e' vuota{dove}.")
        return 0
    _stampa(testo)
    return 0


def cmd_chiudi(args):
    mia = _identita(args)
    rec = store.chiudi(args.id, mia, esito=args.esito)
    print(f"{args.id} chiusa da {mia['sessione'][:8]} ({rec['id']})")
    return 0


def cmd_chi(args):
    vive = sessioni.vive()
    if not vive:
        print("nessuna sessione viva conosciuta. boa conosce solo chi e' passato dal suo hook.")
        return 0
    for v in vive:
        peso = sessioni.peso_transcript(v["sessione"])
        p = f"{peso / (1024 * 1024):.1f} MB" if peso is not None else "transcript ignoto"
        print(f"{v['sessione'][:8]}  {v['progetto'] or '?':<16}  vista {v['eta'] / 60:5.1f} min fa  "
              f"{p:>16}  {v['cwd']}")
    return 0


def cmd_hook(args):
    """Sempre zero, sempre una riga di json sullo standard output.

    Non c'e' nessun ramo che possa uscire diverso da zero. Un hook che fallisce ferma il
    prompt della sessione che lo ospita, e boa non vale il rischio di fermare il lavoro
    di qualcun altro.
    """
    try:
        dati = sys.stdin.read()
    except Exception:
        dati = ""
    try:
        risposta = consegna.hook(dati)
    except Exception:
        risposta = "{}"
    try:
        print(risposta)
        sys.stdout.flush()
    except Exception:
        pass
    # Il `pass` qui sopra non bastava, e la revisione avversariale lo ha
    # dimostrato: con lo standard output su una pipe gia' chiusa, print
    # fallisce, l'eccezione viene assorbita, ma poi CPython prova a svuotare
    # sys.stdout mentre chiude l'interprete, fallisce di nuovo dove nessuno
    # puo' piu' prenderlo, e il processo esce con 120 stampando
    # "Exception ignored ... BrokenPipeError" sullo stderr. Un hook che esce
    # con 120 e' esattamente la cosa che questo docstring promette che non
    # succede. Si sostituisce lo stdout con qualcosa che si puo' chiudere
    # senza rumore, e la promessa torna vera.
    try:
        sys.stdout = open(os.devnull, "w")
    except Exception:
        pass
    return 0


def cmd_registro(args):
    """Chi ha scritto quanto, e con che identita'. E' l'audit della lavagna.

    Serve perche' le difese di boa sono tutte dentro la consegna, cioe' agiscono
    su chi legge. Questa guarda dall'altra parte: chi scrive, quanto, e con che
    nome. Una sessione che scrive molto piu' delle altre, o che scrive sempre
    con identita' dichiarata, e' la cosa che si vuole poter vedere senza dover
    leggere la lavagna riga per riga.
    """
    voci = store.tutte()
    if not voci:
        print("la lavagna e' vuota.")
        return 0
    per_autore = {}
    for v in voci:
        da = v.get("da") or {}
        chiave = (da.get("sessione") or "anonimo", da.get("prova") or "anonima")
        r = per_autore.setdefault(chiave, {"n": 0, "tipi": {}, "prog": set(), "ultima": 0})
        r["n"] += 1
        r["tipi"][v.get("tipo", "?")] = r["tipi"].get(v.get("tipo", "?"), 0) + 1
        if da.get("progetto"):
            r["prog"].add(da["progetto"])
        r["ultima"] = max(r["ultima"], v.get("ts") or 0)
    tot = len(voci)
    print(f"{tot} voci sulla lavagna, {len(per_autore)} autori\n")
    print(f"{'sessione':<10}{'identita':<13}{'voci':>6}{'quota':>7}  tipi / progetti")
    for (sid, prova), r in sorted(per_autore.items(), key=lambda kv: -kv[1]["n"]):
        tipi = ", ".join(f"{k}x{v}" for k, v in sorted(r["tipi"].items(), key=lambda kv: -kv[1]))
        prog = ", ".join(sorted(r["prog"]))[:28]
        print(f"{sid[:8]:<10}{prova:<13}{r['n']:>6}{r['n'] * 100 // tot:>6}%  {tipi}"
              + (f"   [{prog}]" if prog else ""))
    dominante = max(per_autore.items(), key=lambda kv: kv[1]["n"])
    if dominante[1]["n"] * 2 > tot and tot >= 6:
        print(f"\n  {dominante[0][0][:8]} ha scritto piu' della meta' della lavagna.")
    dich = sum(r["n"] for (s_, pr), r in per_autore.items() if pr == "dichiarata")
    if dich:
        print(f"  {dich} voci con identita' solo dichiarata: quel nome non l'ha "
              f"verificato nessuno.")
    return 0


def cmd_manda(args):
    mia = _identita(args)
    dest = store.safe(args.sessione)
    if not dest:
        print("boa: il destinatario di manda e' un id di sessione", file=sys.stderr)
        return 2
    try:
        rec = store.scrivi(mia, a=dest, tipo=args.tipo, testo=args.testo)
    except ValueError as e:
        print(f"boa: {e}", file=sys.stderr)
        return 2
    if not args.ora:
        # senza --ora non si riprende nessuna sessione. La voce e' sulla lavagna, e
        # l'hook della destinataria la consegnera' al suo prossimo prompt.
        print(f"{rec['id']} scritta per {dest[:8]}: arriva al suo prossimo turno. "
              "Con --ora la sessione viene ripresa adesso.")
        return 0
    partita, spiegazione = consegna.spingi(dest, rec)
    print(f"{rec['id']}: {spiegazione}")
    return 0 if partita else 1


# ---------------------------------------------------------------------------- avvio

def build():
    p = argparse.ArgumentParser(prog="boa", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"boa {__version__}")
    p.add_argument("--io", metavar="UUID", help="dichiara la sessione che sta parlando")
    sub = p.add_subparsers(dest="verbo")

    s = sub.add_parser("scrivi", help="aggiunge una voce alla lavagna")
    s.add_argument("testo")
    s.add_argument("--a", default=store.TUTTI,
                   metavar="DEST", help="tutti, progetto:<nome>, oppure un id di sessione")
    s.add_argument("--tipo", default="messaggio", choices=store.TIPI)
    s.add_argument("--riferimento", metavar="ID", help="l'id della voce a cui rispondi")
    s.add_argument("--una-volta", action="store_true", dest="una_volta",
                   help="non riscrivere se l'hai gia' detta da meno di un'ora")
    s.add_argument("--chiave", metavar="K",
                   help="cosa rende due voci 'la stessa notizia', se non il testo")
    s.add_argument("--ambito", choices=("sessione", "macchina"), default="sessione",
                   help="di chi e' la notizia: tua (default) o della macchina. "
                        "Con macchina, chi la dice non conta e il doppione e' un "
                        "doppione anche se lo scrive un'altra sessione")
    s.set_defaults(fn=cmd_scrivi)

    s = sub.add_parser("leggi", help="le voci per me che non ho gia' visto")
    s.set_defaults(fn=cmd_leggi)

    s = sub.add_parser("lavagna", help="tutto quello che e' aperto, di tutti")
    s.add_argument("--progetto", metavar="NOME")
    s.set_defaults(fn=cmd_lavagna)

    s = sub.add_parser("chiudi", help="dichiara finita una voce")
    s.add_argument("id")
    s.add_argument("esito", nargs="?", default="")
    s.set_defaults(fn=cmd_chiudi)

    s = sub.add_parser("registro", help="chi ha scritto quanto, e con che identita'")
    s.set_defaults(fn=cmd_registro)

    s = sub.add_parser("chi", help="quali sessioni sono vive adesso")
    s.set_defaults(fn=cmd_chi)

    s = sub.add_parser("hook", help="legge il payload di un hook su stdin")
    s.set_defaults(fn=cmd_hook)

    s = sub.add_parser("manda", help="scrive per una sessione, e con --ora la riprende")
    s.add_argument("sessione")
    s.add_argument("testo")
    s.add_argument("--tipo", default="messaggio", choices=store.TIPI)
    s.add_argument("--ora", action="store_true",
                   help="riprende la sessione adesso invece di aspettare il suo turno")
    s.set_defaults(fn=cmd_manda)
    return p


def main(argv=None):
    # Le due uscite stanno dentro main e non sotto __main__ perche' adesso boa si
    # installa: il comando che scrive pip chiama main() e basta, e da li' non si
    # passa mai per __main__. Fuori di qui un `boa lavagna | head` tornava una
    # traccia invece di zero.
    try:
        p = build()
        args = p.parse_args(argv)
        if not getattr(args, "fn", None):
            p.print_help()
            return 0
        return args.fn(args)
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
