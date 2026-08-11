"""La lavagna: righe che si aggiungono, e non si tolgono mai.

Tre file sotto ~/.boa, e niente altrove:

  lavagna.jsonl     una riga per voce, aperta in append e mai riscritta
  chiuse.jsonl      una riga per voce dichiarata finita
  letto/<id>.json   il segnalibro di una sessione, l'unico file che si riscrive

Perche' append e non un file di stato con un lock. Qui scrivono sessioni che non si
conoscono e non si aspettano. Un lock e' una cosa che si puo' perdere, e chi la perde
resta fuori proprio mentre aveva qualcosa da dire; una riga in append non ha finestre,
non ha proprietario e non ha niente da rilasciare. Il prezzo e' che lo stato di una voce
non e' un campo che si modifica ma la somma delle righe che la riguardano, e si
ricalcola a ogni lettura. E' un prezzo che si paga volentieri: rada ha gia' pagato una
volta il prezzo opposto, e il difetto era due job partiti insieme.

L'atomicita' della scrittura non viene da un lock ma da due cose che valgono insieme:

  1. il file si apre con O_APPEND, che sposta la fine e scrive in una operazione sola,
     quindi due processi non si sovrascrivono a vicenda;
  2. la riga sta sotto LIMITE_RIGA byte e viene scritta con una os.write() sola, quindi
     non esiste il caso in cui il kernel ne scrive meta' e lascia l'altra meta' a un
     secondo giro, in mezzo al quale un altro processo puo' infilarsi.

Una voce piu' lunga del limite viene accorciata prima di essere scritta, mai spezzata in
due write. Perdere la coda di un testo e' un danno; una riga corrotta sulla lavagna e'
un danno per tutti quelli che leggono dopo.

Chi legge tollera l'ultima riga tronca e la salta, e il segnalibro non la scavalca: se un
processo e' stato ucciso a meta' write, la sua voce arrivera' quando la riga sara' intera.
"""
import json
import os
import time
import uuid

HOME = os.path.expanduser(os.environ.get("BOA_HOME", "~/.boa"))
LAVAGNA = os.path.join(HOME, "lavagna.jsonl")
CHIUSE = os.path.join(HOME, "chiuse.jsonl")
LETTO = os.path.join(HOME, "letto")

TIPI = ("messaggio", "preso", "fatto", "domanda", "avviso")
TUTTI = "tutti"
PROG = "progetto:"

# Il tetto entro cui una riga e' scritta da una write() sola. Sotto questa soglia una
# append su file regolare non torna mai a meta' su macOS ne' su Linux; sopra, la
# garanzia dipende dal filesystem, e la lavagna smetterebbe di essere append-only nel
# solo modo che conta, cioe' quando due sessioni scrivono nello stesso istante.
LIMITE_RIGA = 4096

TRONCATO = " [troncato]"

# Quante voci consegna al massimo una lettura. L'hook gira a ogni prompt di ogni
# sessione: senza tetto, una sessione tornata dopo un giorno si prenderebbe in contesto
# tutto quello che e' successo mentre non c'era.
TETTO = 12

# Quanto legge una volta sola chi riprende dal segnalibro. Il segnalibro avanza solo di
# quello che e' stato letto davvero, quindi un arretrato piu' grande di cosi' si smaltisce
# al prompt dopo invece di arrivare tutto insieme.
LETTURA_MAX = 1 << 20

# Quanto legge `boa lavagna`, dalla fine. La lavagna cresce di una riga alla volta e solo
# quando qualcuno decide di scriverci, quindi in pratica non si arriva mai a questo tetto;
# c'e' perche' un comando di lettura non deve poter allocare quanto pesa un file.
LETTURA_TOTALE = 8 << 20


def ensure_home():
    for d in (HOME, LETTO):
        os.makedirs(d, exist_ok=True)


def safe(sid):
    """Un id di sessione e' un uuid, ma non si costruisce un percorso su una promessa.

    Vale anche qui e non solo in sessioni.py perche' l'id arriva dal payload di un hook,
    cioe' da fuori, e finisce nel nome del file del segnalibro.
    """
    if not isinstance(sid, str):
        return None
    keep = "".join(c for c in sid if c.isalnum() or c in "-_")[:64]
    return keep or None


def _ora():
    return round(time.time(), 3)


def _nuovo_id():
    return uuid.uuid4().hex[:6]


def norm_a(a):
    """Normalizza il destinatario. Le forme sono tre e non di piu'."""
    if not a:
        return TUTTI
    a = str(a).strip()
    if not a or a == TUTTI:
        return TUTTI
    if a.startswith(PROG):
        nome = a[len(PROG):].strip()
        return PROG + nome if nome else TUTTI
    s = safe(a)
    return s or TUTTI


def _codifica(rec):
    return (json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _riga(rec):
    """La voce, come byte, garantita sotto LIMITE_RIGA.

    Accorcia `testo`, che e' l'unico campo che puo' crescere senza limite. Se anche
    svuotandolo la riga resta troppo lunga, solleva invece di scrivere: una riga oltre il
    limite non e' piu' coperta dalla garanzia di atomicita', e scriverla comunque
    significherebbe corrompere la lavagna di tutti per salvare una voce sola.
    """
    riga = _codifica(rec)
    if len(riga) <= LIMITE_RIGA:
        return riga
    # si accorcia il testo di partenza, non quello gia' accorciato: rimettere ogni volta
    # il marcatore su un testo che lo ha gia' fa togliere e riaggiungere gli stessi
    # caratteri all'infinito, e la riga non scende mai sotto il limite.
    originale = rec.get("testo") or ""
    quanti = len(originale)
    for _ in range(64):
        if len(riga) <= LIMITE_RIGA:
            return riga
        if quanti <= 0:
            break
        eccesso = len(riga) - LIMITE_RIGA
        # un carattere puo' pesare piu' di un byte, quindi togliere `eccesso` caratteri
        # toglie almeno `eccesso` byte: il giro converge sempre, e in pratica in due
        quanti = max(0, quanti - max(1, eccesso))
        rec = dict(rec, testo=(originale[:quanti] + TRONCATO) if quanti else "")
        riga = _codifica(rec)
    if len(riga) <= LIMITE_RIGA:
        return riga
    raise ValueError("voce troppo lunga per una riga sola anche a testo vuoto")


def _append(path, rec):
    ensure_home()
    riga = _riga(rec)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        scritti = os.write(fd, riga)
    finally:
        os.close(fd)
    if scritti != len(riga):
        # sotto LIMITE_RIGA non succede. Se un giorno succede, chi ha scritto deve
        # saperlo subito: la sua voce e' meta' sulla lavagna, e i lettori la salteranno.
        raise IOError(f"scrittura parziale: {scritti} byte su {len(riga)}")
    return rec


# ------------------------------------------------------------------------ scrivere

# Due voci uguali dello stesso tipo, entro questa finestra, sono la stessa voce
# detta due volte. Un'ora: abbastanza da assorbire un automatismo che scatta a
# ogni avvio di sessione, poco da non nascondere un fatto che cambia.
FINESTRA_DOPPIONE = 3600


def gia_detta(da, tipo, testo, entro=FINESTRA_DOPPIONE, path=None, chiave=None,
              ambito="sessione"):
    """L'id di una voce identica e recente, o None.

    Nata la notte dell'11/08/2026 da un guasto vero: `faro annuncia` era stato
    agganciato allo SessionStart, che scatta molto piu' spesso di quanto
    immaginassi, e in un'ora aveva messo sulla lavagna dodici avvisi quasi
    identici. boa poi li consegnava tutti e dodici dentro ogni sessione a ogni
    prompt. Il difetto era di faro, ma il posto giusto dove ripararlo e' qui:
    una lavagna deve reggere a uno scrittore rumoroso chiunque esso sia, e non
    c'e' ragione perche' ogni scrittore debba tenersi uno stato suo per
    ricordare cosa ha gia' detto.
    """
    # L'ambito dice di chi e' la notizia, e quindi contro chi va confrontata.
    #
    # `sessione`: la notizia appartiene a chi scrive, e due sessioni diverse che
    # dicono la stessa cosa stanno dicendo due cose. E' il caso normale.
    #
    # `macchina`: la notizia e' un fatto della macchina, e allora chi la dice
    # non conta. Aggiunto la mattina dell'11/08/2026 davanti a un guasto che la
    # prima deduplica non copriva: sei sessioni diverse avevano scritto sei
    # volte "la macchina e' in swap", una per sessione, e per la lavagna erano
    # sei autori distinti quindi sei notizie distinte. Per chi leggeva erano la
    # stessa riga sei volte.
    sessione = (da or {}).get("sessione") or "anonimo"
    testo = "" if testo is None else str(testo)
    limite = _ora() - entro
    # 64 KB di coda: qualche centinaio di voci, che con una lavagna su cui
    # scrivono solo i modelli e solo di proposito copre molto piu' di un'ora.
    for v in reversed(_leggi_coda(path or LAVAGNA, 64 * 1024)):
        if v.get("ts", 0) < limite:
            break
        if v.get("tipo") != tipo:
            continue
        if ambito != "macchina" and (v.get("da") or {}).get("sessione") != sessione:
            continue
        # Con una chiave si confronta la chiave, non il testo. Serve perche' la
        # stessa notizia cambia parole a ogni giro: "swap 4,7 GB, 233829
        # pageout" e "swap 4,7 GB, 236196 pageout" sono la stessa notizia, e un
        # confronto sul testo non le aggancerebbe mai. Cosa sia "la stessa
        # notizia" lo sa chi scrive, non la lavagna: boa si limita a onorare la
        # chiave che le viene data.
        if chiave:
            if v.get("chiave") == chiave:
                return v.get("id")
        elif v.get("testo") == testo:
            return v.get("id")
    return None


def scrivi(da, a=TUTTI, tipo="messaggio", testo="", riferimento=None, path=None,
           una_volta=False, chiave=None, ambito="sessione"):
    """Aggiunge una voce. `da` e' l'identita' che restituisce sessioni.identita().

    Con `una_volta`, se la stessa voce e' gia' sulla lavagna da meno di un'ora
    non ne scrive un'altra e restituisce None. Chi chiama capisce dalla
    risposta se ha detto qualcosa di nuovo o si e' ripetuto.
    """
    if tipo not in TIPI:
        raise ValueError(f"tipo sconosciuto: {tipo!r}, i tipi sono {', '.join(TIPI)}")
    da = da or {}
    if una_volta and gia_detta(da, tipo, testo, path=path, chiave=chiave,
                               ambito=ambito):
        return None
    rec = {
        "id": _nuovo_id(),
        "ts": _ora(),
        "da": {
            "sessione": da.get("sessione") or "anonimo",
            "progetto": da.get("progetto") or "",
            "cwd": da.get("cwd") or "",
        },
        "a": norm_a(a),
        "tipo": tipo,
        "testo": "" if testo is None else str(testo),
    }
    if riferimento:
        rec["riferimento"] = str(riferimento)[:64]
    if chiave:
        rec["chiave"] = str(chiave)[:64]
    if ambito != "sessione":
        rec["ambito"] = str(ambito)[:32]
    return _append(path or LAVAGNA, rec)


def chiudi(voce_id, da, esito="", path=None):
    """Dichiara finita una voce. Aggiunge una riga, non ne toglie nessuna."""
    da = da or {}
    rec = {
        "id": _nuovo_id(),
        "ts": _ora(),
        "da": {
            "sessione": da.get("sessione") or "anonimo",
            "progetto": da.get("progetto") or "",
            "cwd": da.get("cwd") or "",
        },
        "chiude": str(voce_id)[:64],
        "testo": "" if esito is None else str(esito),
    }
    return _append(path or CHIUSE, rec)


# ------------------------------------------------------------------------- leggere

def _righe_complete(blob):
    """(righe intere, byte consumati). Quello che sta dopo l'ultimo \\n non e' una riga.

    E' qui che si decide che una scrittura interrotta non fa danno: i byte a meta' non
    vengono ne' letti ne' contati, quindi il segnalibro si ferma prima di loro e la voce
    arrivera' quando sara' intera.
    """
    if not blob:
        return [], 0
    fine = blob.rfind(b"\n")
    if fine < 0:
        return [], 0
    intero = blob[:fine + 1]
    return intero.split(b"\n")[:-1], len(intero)


def _voci(righe):
    """Trasforma in voci le righe che si riescono a leggere, e salta le altre.

    Saltare invece di sollevare e' la ragione per cui una riga tronca, un file mezzo
    sovrascritto o una versione futura di boa non fermano una sessione.
    """
    out = []
    for r in righe:
        if not r.strip():
            continue
        try:
            v = json.loads(r.decode("utf-8"))
        except Exception:
            continue
        if not (isinstance(v, dict) and isinstance(v.get("id"), str)):
            continue
        # json.loads accetta \ud800, cioe' una surrogata spaiata, e produce una
        # str che poi non si puo' ricodificare in utf-8. La lavagna e'
        # append-only e non si ripulisce mai: una riga cosi', scritta a mano una
        # volta sola, faceva morire `boa lavagna` e `boa leggi` con
        # UnicodeEncodeError da li' in avanti, e `leggi` aveva gia' spostato il
        # segnalibro prima di stampare, quindi le voci erano anche perse.
        # Una voce che non si puo' stampare vale come una riga illeggibile:
        # si salta, e la lavagna continua a funzionare.
        try:
            json.dumps(v, ensure_ascii=False).encode("utf-8")
        except Exception:
            continue
        out.append(v)
    return out


def _leggi_coda(path, quanti):
    try:
        dim = os.path.getsize(path)
    except OSError:
        return []
    da = max(0, dim - quanti)
    try:
        with open(path, "rb") as f:
            f.seek(da)
            blob = f.read()
    except OSError:
        return []
    if da > 0:
        # il primo pezzo di riga appartiene a una riga cominciata prima: non e' tronca,
        # e' solo fuori dalla finestra, e va scartata allo stesso modo
        taglio = blob.find(b"\n")
        blob = blob[taglio + 1:] if taglio >= 0 else b""
    righe, _ = _righe_complete(blob)
    return _voci(righe)


def tutte(path=None):
    """Tutte le voci leggibili, dalla fine della lavagna."""
    return _leggi_coda(path or LAVAGNA, LETTURA_TOTALE)


def registro_chiuse(path=None):
    return _leggi_coda(path or CHIUSE, LETTURA_TOTALE)


# ---------------------------------------------------------------------- segnalibro

def _segnalibro(sessione):
    s = safe(sessione)
    if not s:
        return None
    return os.path.join(LETTO, s + ".json")


def letto_fino_a(sessione):
    p = _segnalibro(sessione)
    if not p:
        return 0
    try:
        with open(p) as f:
            d = json.load(f)
        off = int(d.get("offset") or 0)
    except Exception:
        return 0
    return max(0, off)


def _segna(sessione, offset, ultimo=None):
    """Riscrive il segnalibro. E' l'unico file che si riscrive, e ha un padrone solo."""
    p = _segnalibro(sessione)
    if not p:
        return
    try:
        ensure_home()
        tmp = f"{p}.tmp{os.getpid()}"
        with open(tmp, "w") as f:
            json.dump({"offset": int(offset), "ts": _ora(), "ultimo": ultimo}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except Exception:
        # perdere un segnalibro significa rileggere qualcosa, non fermare una sessione
        pass


def per_me(voce, sessione, progetto=None):
    """La voce e' per chi la sta leggendo?"""
    da = voce.get("da") or {}
    mittente = da.get("sessione")
    # una sessione non riceve quello che ha scritto lei. "anonimo" non e' una identita':
    # due anonimi diversi sono due sessioni diverse, e non vanno confusi in uno solo.
    if sessione and sessione != "anonimo" and mittente == sessione:
        return False
    a = voce.get("a") or TUTTI
    if a == TUTTI:
        return True
    if a.startswith(PROG):
        return bool(progetto) and a[len(PROG):] == progetto
    return bool(sessione) and a == sessione


def nuove(sessione, progetto=None, sposta=True, tetto=TETTO):
    """Le voci per me che non ho ancora visto, e sposta il segnalibro.

    Riprende dal byte dove si era fermata invece di rileggere la lavagna: e' quello che
    permette all'hook di costare poco anche quando la lavagna e' lunga.
    """
    off = letto_fino_a(sessione)
    try:
        dim = os.path.getsize(LAVAGNA)
    except OSError:
        return []
    if off > dim:
        # la lavagna e' stata rifatta sotto ai piedi del segnalibro (~/.boa cancellata,
        # e poi ricominciata). Ripartire dall'inizio rilegge; ripartire da un offset
        # oltre la fine non leggerebbe mai piu' niente.
        off = 0
    try:
        with open(LAVAGNA, "rb") as f:
            f.seek(off)
            blob = f.read(LETTURA_MAX)
    except OSError:
        return []
    righe, usati = _righe_complete(blob)

    # Una riga sola piu' lunga della finestra di lettura fermava la consegna per
    # sempre, e in silenzio: senza un \n dentro i byte letti, `usati` restava 0,
    # il segnalibro non si spostava mai piu', e da quel momento nessuna sessione
    # riceveva piu' niente, ne' quella voce ne' tutte quelle scritte dopo.
    # boa non scrive righe cosi' (LIMITE_RIGA le rifiuta), ma la lavagna e' un
    # file e chiunque puo' scriverci a mano. Se la finestra e' piena e non c'e'
    # un a capo, quella riga si scavalca: si perde una voce malformata invece di
    # perdere tutte le voci future.
    if not usati and len(blob) >= LETTURA_MAX:
        salto = blob.find(b"\n")
        if salto < 0:
            # nemmeno oltre la finestra: si va a cercare il prossimo a capo.
            salto = _prossimo_a_capo(off + len(blob))
            if salto is None:
                return []
            _segna(sessione, salto + 1, None) if sposta else None
            return []
        _segna(sessione, off + salto + 1, None) if sposta else None
        return []

    # Il tetto limita quante voci entrano in un contesto in una volta, e serve.
    # Ma prima il segnalibro avanzava su tutti i byte letti mentre venivano
    # consegnate solo le ultime `tetto`: le altre sparivano per sempre, senza
    # che nessuno se ne accorgesse. Una sessione rumorosa che scriveva dodici
    # voci prima del turno di un'altra ne cancellava in silenzio il messaggio.
    #
    # Adesso si consegnano le piu' vecchie, che e' l'ordine in cui si legge, e
    # il segnalibro si ferma su quello che e' stato davvero consegnato. Il
    # resto arriva al giro dopo. Il tetto protegge il contesto senza perdere
    # niente: erano due cose diverse messe in mano allo stesso numero.
    mie, fine, contate = [], 0, 0
    scorso = 0
    for r in righe:
        scorso += len(r) + 1  # il \n che _righe_complete ha tolto
        v = _voci([r])
        if not v:
            fine = scorso
            continue
        if not per_me(v[0], sessione, progetto):
            fine = scorso
            continue
        if tetto and contate >= tetto:
            break
        mie.append(v[0])
        contate += 1
        fine = scorso

    if sposta and fine:
        _segna(sessione, off + fine, mie[-1]["id"] if mie else None)
    return mie


def _prossimo_a_capo(da, quanto=8 * 1024 * 1024):
    """Il byte dopo il prossimo a capo a partire da `da`, o None se non c'e'.

    Serve solo alla via di fuga qui sopra: una riga scritta a mano piu' lunga
    della finestra di lettura. Legge a blocchi per non tirarsi in memoria un
    file che qualcuno ha gonfiato apposta.
    """
    passo = 1024 * 1024
    try:
        with open(LAVAGNA, "rb") as f:
            f.seek(da)
            letti = 0
            while letti < quanto:
                blocco = f.read(passo)
                if not blocco:
                    return None
                i = blocco.find(b"\n")
                if i >= 0:
                    return da + letti + i
                letti += len(blocco)
    except OSError:
        return None
    return None


# ------------------------------------------------------------------- stato di una voce

def ids_chiuse(voci=None, chiuse=None):
    """Gli id delle voci finite.

    Due modi di finire, e sono lo stesso fatto detto da due parti: una riga in
    chiuse.jsonl, oppure una voce di tipo `fatto` che ne cita un'altra. Il secondo e' il
    motivo per cui `preso` e `fatto` bastano a fare un tasklist condiviso senza aggiungere
    un tipo `task`: una voce presa e non ancora citata da un `fatto` e' un lavoro in corso.
    """
    voci = tutte() if voci is None else voci
    chiuse = registro_chiuse() if chiuse is None else chiuse
    fin = set()
    for c in chiuse:
        r = c.get("chiude")
        if isinstance(r, str):
            fin.add(r)
    for v in voci:
        if v.get("tipo") == "fatto" and isinstance(v.get("riferimento"), str):
            fin.add(v["riferimento"])
    return fin


def tocca_progetto(voce, progetto):
    da = voce.get("da") or {}
    if da.get("progetto") == progetto:
        return True
    return (voce.get("a") or "") == PROG + progetto


def aperte(progetto=None, limite=100):
    """Quello che e' aperto, di tutti. Non tocca nessun segnalibro.

    Guardare la lavagna non deve far sparire niente: chi guarda vuole sapere cosa c'e',
    e se guardare consumasse, nessuno guarderebbe due volte.
    """
    voci = tutte()
    fin = ids_chiuse(voci)
    out = [v for v in voci if v["id"] not in fin]
    if progetto:
        out = [v for v in out if tocca_progetto(v, progetto)]
    return out[-limite:] if limite else out
