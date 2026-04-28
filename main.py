from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from supabase import create_client
from pydantic import BaseModel
from typing import Optional
import os, random, string, logging

# ════════════════════════════════════════════════
# CONFIGURATION
# ════════════════════════════════════════════════
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Rate Limiter ──
limiter = Limiter(key_func=get_remote_address)

app = FastAPI()

# ── Gestionnaire d'erreur rate limit ──
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_KEY"]
)

# ════════════════════════════════════════════════
# MODELES
# ════════════════════════════════════════════════
class Inscription(BaseModel):
    nom:              str
    prenom:           str
    telephone:        Optional[str] = None
    email:            Optional[str] = None
    origine:          Optional[str] = None
    est_interne:      bool = True
    type_ticket:      str
    evenement_id:     Optional[str] = None
    operateur:        Optional[str] = None
    code_transaction: Optional[str] = None
    montant:          Optional[int] = None  # ignoré — calculé côté serveur

class StatutUpdate(BaseModel):
    statut: str

# ════════════════════════════════════════════════
# UTILITAIRES
# ════════════════════════════════════════════════
def gen_ticket(type_ticket: str) -> str:
    prefix = "SO" if type_ticket == "soiree" else "SW"
    code   = ''.join(random.choices(
        string.ascii_uppercase + string.digits, k=8
    ))
    return f"{prefix}{code}"

# ════════════════════════════════════════════════
# HEALTH CHECK
# ════════════════════════════════════════════════
@app.get("/")
def root():
    return {"status": "ok", "service": "Semaine 2026 API"}

@app.get("/health")
def health():
    return {"status": "ok"}

# ════════════════════════════════════════════════
# INSCRIPTIONS — GET (compteur pour la page HTML)
# ════════════════════════════════════════════════
@app.get("/api/inscriptions")
@limiter.limit("30/minute")   # 30 lectures/min par IP
def get_inscriptions(request: Request, limit: int = 500):
    try:
        res = supabase.table("inscriptions")\
            .select("*")\
            .order("created_at", desc=True)\
            .limit(limit)\
            .execute()
        return res.data
    except Exception as e:
        logger.error(f"ERREUR get_inscriptions : {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

# ════════════════════════════════════════════════
# INSCRIPTIONS — POST (création depuis page HTML)
# ════════════════════════════════════════════════
@app.post("/api/inscriptions")
@limiter.limit("5/minute")    # max 5 tentatives/min par IP
def create_inscription(request: Request, data: Inscription):

    # ════════════════════════════════
    # BLOC 1 — VALIDATIONS DE BASE
    # ════════════════════════════════

    # Nom
    if not data.nom or len(data.nom.strip()) < 2:
        logger.warning(f"REJETÉ — nom invalide : '{data.nom}'")
        raise HTTPException(
            status_code=400,
            detail="Inscription invalide"
        )

    # Prénom
    if not data.prenom or len(data.prenom.strip()) < 2:
        logger.warning(f"REJETÉ — prénom invalide : '{data.prenom}'")
        raise HTTPException(
            status_code=400,
            detail="Inscription invalide"
        )

    # Téléphone
    if not data.telephone or len(data.telephone.strip()) < 8:
        logger.warning(
            f"REJETÉ — téléphone invalide : '{data.telephone}'"
        )
        raise HTTPException(
            status_code=400,
            detail="Inscription invalide"
        )

    # Code transaction
    if not data.code_transaction or \
       len(data.code_transaction.strip()) < 3:
        logger.warning(
            f"REJETÉ — code transaction invalide : "
            f"'{data.code_transaction}'"
        )
        raise HTTPException(
            status_code=400,
            detail="Inscription invalide"
        )

    # Variables nettoyées
    tel_clean = data.telephone.strip()
    nom_clean = data.nom.strip().lower()
    prn_clean = data.prenom.strip().lower()
    txn_clean = data.code_transaction.strip()

    # ════════════════════════════════
    # BLOC 2 — LIMITES ANTI-FRAUDE
    # ════════════════════════════════
    try:

        # Limite par téléphone — max 20
        res_tel = supabase.table("inscriptions")\
            .select("id", count="exact")\
            .eq("telephone", tel_clean)\
            .execute()

        count_tel = res_tel.count or 0

        if count_tel >= 20:
            logger.warning(
                f"🚫 LIMITE TÉLÉPHONE — {tel_clean} | "
                f"{data.nom} {data.prenom} | count={count_tel}"
            )
            raise HTTPException(
                status_code=429,
                detail="Inscription invalide"
            )

        # Limite par nom + prénom — max 20
        res_nom = supabase.table("inscriptions")\
            .select("id", count="exact")\
            .ilike("nom",    nom_clean)\
            .ilike("prenom", prn_clean)\
            .execute()

        count_nom = res_nom.count or 0

        if count_nom >= 20:
            logger.warning(
                f"🚫 LIMITE NOM — {nom_clean} {prn_clean} | "
                f"tel={tel_clean} | count={count_nom}"
            )
            raise HTTPException(
                status_code=429,
                detail="Inscription invalide"
            )

        # Code transaction unique — max 1 utilisation
        res_txn = supabase.table("inscriptions")\
            .select("id", count="exact")\
            .eq("code_transaction", txn_clean)\
            .execute()

        count_txn = res_txn.count or 0

        if count_txn >= 1:
            logger.warning(
                f"🚫 CODE TRANSACTION DÉJÀ UTILISÉ — "
                f"txn={txn_clean} | tel={tel_clean} | "
                f"{data.nom} {data.prenom}"
            )
            raise HTTPException(
                status_code=429,
                detail="Inscription invalide"
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"ERREUR vérification limites : {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

    # ════════════════════════════════
    # BLOC 3 — MONTANT DEPUIS LA BDD
    # ════════════════════════════════
    try:
        ticket_type_res = supabase.table("ticket_types")\
            .select("id, nom, prix, type_code, actif")\
            .eq("actif", True)\
            .or_(
                f"type_code.eq.{data.type_ticket},"
                f"id.eq.{data.type_ticket}"
            )\
            .limit(1)\
            .execute()
    except Exception as e:
        logger.error(f"ERREUR lecture ticket_types : {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

    if not ticket_type_res.data:
        logger.warning(
            f"REJETÉ — type ticket invalide : '{data.type_ticket}'"
        )
        raise HTTPException(
            status_code=400,
            detail="Inscription invalide"
        )

    ticket_type_db = ticket_type_res.data[0]

    # Montant officiel depuis la BDD — jamais depuis le client
    montant_reel = ticket_type_db["prix"]

    # Détection tentative de fraude sur le montant
    if data.montant is not None and data.montant != montant_reel:
        logger.warning(
            f"⚠️ FRAUDE MONTANT — {data.nom} {data.prenom} | "
            f"envoyé={data.montant} | réel={montant_reel} | "
            f"tel={tel_clean}"
        )
        # On continue — on enregistre le vrai montant quand même

    # ════════════════════════════════
    # BLOC 4 — INSERTION EN BDD
    # ════════════════════════════════
    ticket = gen_ticket(ticket_type_db["type_code"])

    row = {
        "nom":              data.nom.strip(),
        "prenom":           data.prenom.strip(),
        "telephone":        tel_clean,
        "email":            data.email.strip() if data.email else None,
        "origine":          data.origine or "",
        "est_interne":      data.est_interne,
        "type_ticket":      ticket_type_db["type_code"],
        "evenement_id":     data.evenement_id or None,
        "operateur":        data.operateur or None,
        "code_transaction": txn_clean,
        "montant":          montant_reel,
        "numero_ticket":    ticket,
        "statut":           "pending",
        "billet_utilise":   False
    }

    try:
        res = supabase.table("inscriptions").insert(row).execute()
        logger.info(
            f"✅ INSCRIPTION OK — {data.nom} {data.prenom} | "
            f"{ticket} | {ticket_type_db['nom']} | {montant_reel}F"
        )
        return {
            "numero_ticket": ticket,
            "montant":       montant_reel,
            "ticket_nom":    ticket_type_db["nom"],
            "data":          res.data
        }
    except Exception as e:
        logger.error(f"ERREUR insert inscription : {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

# ════════════════════════════════════════════════
# INSCRIPTIONS — PATCH statut (panel admin)
# ════════════════════════════════════════════════
@app.patch("/api/inscriptions/{ticket}/statut")
@limiter.limit("20/minute")   # actions admin
def update_statut(request: Request, ticket: str, update: StatutUpdate):
    statuts_autorises = ["pending", "valide", "refuse", "rembourse"]
    if update.statut not in statuts_autorises:
        raise HTTPException(
            status_code=400,
            detail="Statut invalide"
        )
    try:
        res = supabase.table("inscriptions")\
            .update({"statut": update.statut})\
            .eq("numero_ticket", ticket)\
            .execute()
        logger.info(f"STATUT MÀJ — {ticket} → {update.statut}")
        return res.data
    except Exception as e:
        logger.error(f"ERREUR update_statut : {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

# ════════════════════════════════════════════════
# INSCRIPTIONS — PATCH utilisé (scan à l'entrée)
# ════════════════════════════════════════════════
@app.patch("/api/inscriptions/{ticket}/utilise")
@limiter.limit("20/minute")
def mark_used(request: Request, ticket: str):
    try:
        res = supabase.table("inscriptions")\
            .update({"billet_utilise": True})\
            .eq("numero_ticket", ticket)\
            .execute()
        logger.info(f"BILLET UTILISÉ — {ticket}")
        return res.data
    except Exception as e:
        logger.error(f"ERREUR mark_used : {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

# ════════════════════════════════════════════════
# TICKET TYPES (affiché sur la page HTML)
# ════════════════════════════════════════════════
@app.get("/api/ticket-types")
@limiter.limit("30/minute")
def get_ticket_types(request: Request):
    try:
        res = supabase.table("ticket_types")\
            .select("*")\
            .eq("actif", True)\
            .order("ordre")\
            .execute()
        return res.data
    except Exception as e:
        logger.error(f"ERREUR get_ticket_types : {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

# ════════════════════════════════════════════════
# PLANNING (affiché sur la page HTML)
# ════════════════════════════════════════════════
@app.get("/api/planning")
@limiter.limit("30/minute")
def get_planning(request: Request):
    try:
        res = supabase.table("planning")\
            .select("*")\
            .order("date")\
            .order("heure")\
            .execute()
        return res.data
    except Exception as e:
        logger.error(f"ERREUR get_planning : {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

# ════════════════════════════════════════════════
# EVENEMENTS
# ════════════════════════════════════════════════
@app.get("/api/evenements")
@limiter.limit("30/minute")
def get_evenements(request: Request):
    try:
        res = supabase.table("evenements")\
            .select("*")\
            .order("date_evt")\
            .order("heure_debut")\
            .execute()
        return res.data
    except Exception as e:
        logger.error(f"ERREUR get_evenements : {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

@app.post("/api/evenements")
@limiter.limit("10/minute")
def upsert_evenement(request: Request, data: dict):
    try:
        res = supabase.table("evenements").upsert(data).execute()
        return res.data
    except Exception as e:
        logger.error(f"ERREUR upsert_evenement : {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

# ════════════════════════════════════════════════
# COMMENTAIRES (affiché sur la page HTML)
# ════════════════════════════════════════════════
@app.get("/api/commentaires/{evenement_id}")
@limiter.limit("30/minute")
def get_commentaires(request: Request, evenement_id: str):
    try:
        res = supabase.table("commentaires")\
            .select("*")\
            .eq("evenement_id", evenement_id)\
            .order("created_at", desc=True)\
            .execute()
        return res.data
    except Exception as e:
        logger.error(f"ERREUR get_commentaires : {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

@app.post("/api/commentaires")
@limiter.limit("10/minute")   # anti-spam commentaires
def post_commentaire(request: Request, data: dict):
    if not data.get("contenu") or \
       len(str(data["contenu"]).strip()) < 2:
        raise HTTPException(
            status_code=400,
            detail="Commentaire vide"
        )
    try:
        res = supabase.table("commentaires").insert(data).execute()
        return res.data
    except Exception as e:
        logger.error(f"ERREUR post_commentaire : {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")
