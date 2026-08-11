"""Chi sono io, e chi altro e' vivo adesso.

Dentro un hook la domanda non si pone: il payload di Claude Code contiene `session_id`,
`cwd` e `transcript_path`, ed e' la fonte buona. Non e' documentata, e' stata verificata a
mano da rada eseguendo sessioni vere con un hook di prova (vedi ~/dev/rada/CLAUDE.md, i
quattro fatti). boa non la reinventa e non la deduce: la riceve e la annota.

Fuori da un hook la domanda si pone, e la risposta e' nell'ordine:

  1. --io <uuid>, perche' chi lo scrive sa quello che dice
  2. $BOA_SESSION
  3. la sessione viva la cui cartella di lavoro coincide con quella corrente
  4. anonimo

Il terzo punto ha un limite che va detto: se due sessioni lavorano nella stessa cartella,
da fuori non si distinguono, e boa risponde `anonimo` invece di tirare a indovinare.
Indovinare male non significa leggere il messaggio sbagliato, significa spostare il
segnalibro di un'altra sessione, e quella sessione non vedra' mai piu' quella voce.

Il progetto si ricava dal cwd e non si chiede mai, cosi' due sessioni nella stessa
cartella lo scrivono uguale senza essersi accordate.
"""
import json
import os
import time

from . import store

BATTITI = os.path.join(store.HOME, "sessioni")
CLAUDE = os.path.expanduser(os.environ.get("BOA_CLAUDE_HOME", "~/.claude"))
ANONIMO = "anonimo"

# Quanto resta "viva" una sessione dopo che si e' fatta sentire l'ultima volta. Lo stesso
# numero di rada, per la stessa ragione: fra un prompt e l'altro una sessione puo' stare
# zitta a lungo mentre legge file e scrive codice.
FINESTRA = float(os.environ.get("BOA_FINESTRA", 900))

# Oltre questo, il battito e' di una sessione chiusa settimane fa e il file va tolto.
DIMENTICA = 86400.0


def _mtime(p):
    try:
        return os.path.getmtime(p)
    except OSError:
        return None


def note(sid, cwd=None, transcript=None, progetto_=None):
    """Segna che questa sessione e' viva, e dove lavora. Non solleva mai.

    La chiama l'hook, che e' l'unico punto in cui boa vede l'identita' vera. Tutto quello
    che `boa chi` sa, lo sa da qui.
    """
    sid = store.safe(sid)
    if not sid:
        return
    try:
        os.makedirs(BATTITI, exist_ok=True)
        d = {
            "sessione": sid,
            "cwd": cwd or "",
            "progetto": progetto_ or (progetto(cwd) if cwd else ""),
            "transcript": transcript or "",
            "ts": time.time(),
        }
        tmp = os.path.join(BATTITI, f".{sid}.tmp{os.getpid()}")
        with open(tmp, "w") as f:
            json.dump(d, f)
        os.replace(tmp, os.path.join(BATTITI, sid + ".json"))
    except Exception:
        pass


def _battito(sid):
    sid = store.safe(sid)
    if not sid:
        return None
    try:
        with open(os.path.join(BATTITI, sid + ".json")) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def vive(now=None, finestra=None):
    """Le sessioni che si sono fatte sentire di recente, la piu' fresca per prima.

    Due orologi, e si prende il piu' avanti dei due: quando la sessione ha passato un
    prompt dall'hook, e quando il suo transcript e' stato scritto l'ultima volta. Il
    secondo esiste perche' una sessione dentro un comando che dura mezz'ora non manda
    prompt, ma il transcript cresce lo stesso, e senza quel segnale sparirebbe da `boa
    chi` proprio mentre e' la piu' occupata di tutte.
    """
    now = now if now is not None else time.time()
    finestra = FINESTRA if finestra is None else finestra
    out = []
    try:
        nomi = os.listdir(BATTITI)
    except OSError:
        return out
    for n in nomi:
        if not n.endswith(".json"):
            continue
        p = os.path.join(BATTITI, n)
        d = None
        try:
            with open(p) as f:
                d = json.load(f)
        except Exception:
            d = None
        if not isinstance(d, dict):
            continue
        visto = d.get("ts") or _mtime(p) or 0
        t = d.get("transcript")
        if t:
            m = _mtime(t)
            if m:
                visto = max(visto, m)
        eta = now - visto
        if eta > DIMENTICA:
            try:
                os.remove(p)
            except OSError:
                pass
            continue
        if eta > finestra:
            continue
        out.append({
            "sessione": d.get("sessione") or n[:-len(".json")],
            "cwd": d.get("cwd") or "",
            "progetto": d.get("progetto") or "",
            "transcript": t or "",
            "eta": eta,
        })
    out.sort(key=lambda s: s["eta"])
    return out


def progetto(cwd=None):
    """Il nome del progetto, ricavato dal cwd e mai chiesto.

    La radice del repository e non la cartella corrente, altrimenti una sessione che
    lavora in `~/dev/faro/tools` scriverebbe `tools` e non incontrerebbe mai le voci
    indirizzate a `progetto:faro`.
    """
    cwd = os.path.realpath(cwd or os.getcwd())
    d = cwd
    for _ in range(12):
        if os.path.exists(os.path.join(d, ".git")):
            return os.path.basename(d) or d
        su = os.path.dirname(d)
        if su == d:
            break
        d = su
    return os.path.basename(cwd.rstrip("/")) or cwd


# Quanto vale il nome che una voce si da'. Non e' una gerarchia di fiducia sul
# contenuto: il contenuto resta non fidato sempre. E' una gerarchia su **chi
# dice di essere**, e serve perche' senza di essa la cornice presentava come un
# fatto ("l'ha scritto la sessione X") una cosa che chiunque poteva affermare.
#
# La domanda di Eugenio, l'11/08/2026: se un agente puo' far comparire testo nel
# contesto di un altro, cosa gli impedisce di fingersi qualcun altro per
# ottenere qualcosa? Prima di oggi: niente.
ATTESTATA = "attestata"    # l'id viene dal payload di un hook: lo dice Claude Code
DEDOTTA = "dedotta"        # una sola sessione viva lavora in questa cartella
DICHIARATA = "dichiarata"  # --io o $BOA_SESSION: lo dice chi scrive, nessuno ha verificato
ANONIMA = "anonima"        # nessuno lo sa


def io(esplicito=None, cwd=None):
    """Chi sono. Restituisce solo l'id: `chi_sono` dice anche quanto vale."""
    return chi_sono(esplicito, cwd)[0]


def chi_sono(esplicito=None, cwd=None, da_hook=None):
    """(id, quanto vale quel nome).

    `da_hook` lo passa soltanto consegna.hook(), che ha letto il payload di
    Claude Code: e' l'unica strada per cui un id vale ATTESTATA. Nessun verbo
    della CLI puo' produrre un'attestazione, per costruzione.
    """
    s = store.safe(da_hook)
    if s:
        return s, ATTESTATA
    s = store.safe(esplicito)
    if s:
        return s, DICHIARATA
    s = store.safe(os.environ.get("BOA_SESSION"))
    if s:
        return s, DICHIARATA
    qui = os.path.realpath(cwd or os.getcwd())
    mie = [v for v in vive() if v.get("cwd") and os.path.realpath(v["cwd"]) == qui]
    if len(mie) == 1:
        return mie[0]["sessione"], DEDOTTA
    return ANONIMO, ANONIMA


def identita(esplicito=None, cwd=None, da_hook=None):
    cwd = cwd or os.getcwd()
    sid, prova = chi_sono(esplicito, cwd, da_hook)
    return {"sessione": sid, "progetto": progetto(cwd), "cwd": cwd, "prova": prova}


def transcript(sid):
    """Il file del transcript di una sessione, o None.

    Prima quello che l'hook ha annotato, che e' la fonte buona. Se manca, si cerca il
    file `<sessione>.jsonl` sotto le cartelle di ~/.claude/projects: sono qualche decina,
    quindi e' qualche decina di stat, e si evita di dover indovinare come Claude Code
    trasforma un percorso nel nome di una cartella.
    """
    sid = store.safe(sid)
    if not sid:
        return None
    d = _battito(sid) or {}
    t = d.get("transcript")
    if t and os.path.exists(t):
        return t
    base = os.path.join(CLAUDE, "projects")
    try:
        cartelle = os.listdir(base)
    except OSError:
        return None
    for c in cartelle:
        p = os.path.join(base, c, sid + ".jsonl")
        if os.path.exists(p):
            return p
    return None


def peso_transcript(sid):
    """Quanto pesa il transcript, in byte, o None se non si trova.

    None e' un risultato, non un errore: chi non riesce a misurare non deve spingere.
    """
    t = transcript(sid)
    if not t:
        return None
    try:
        return os.path.getsize(t)
    except OSError:
        return None
