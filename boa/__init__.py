"""boa: una lavagna sola che tutte le sessioni vedono, su cui scrivono i modelli.

Una boa di ormeggio non appartiene a nessuna barca. Sta ferma, la vedono tutti, e chi
arriva ci si lega. Questo pacchetto e' quella boa: un file in append sotto ~/.boa su cui
una sessione di Claude Code scrive quello che le altre non possono ricavare da sole, e da
cui le altre ricevono senza doversi ricordare di guardare.

Non e' un canale di messaggi e non e' un aggregatore. Scrivono i modelli, di proposito,
quando hanno qualcosa da dire. Se una voce non l'ha scritta qualcuno apposta, non esiste.

L'invariante che tiene in piedi tutto il resto sta in consegna.py: quello che arriva
dalla lavagna e' dato, mai istruzione.

Non c'e' un numero di schema, e la mancanza e' voluta. Il lettore salta ogni riga che non
sa leggere (store._voci), quindi due versioni di boa che scrivono campi diversi convivono
sulla stessa lavagna senza doversi accordare su niente: chi non capisce una riga la
ignora e va avanti. Un numero di schema qui aggiungerebbe un modo di rifiutare righe
valide, non un modo di leggerne di piu'.

La versione sta qui e da nessun'altra parte.
"""
__version__ = "0.1.0"
