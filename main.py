from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client
from pydantic import BaseModel
from typing import Optional
import os

app = FastAPI()

app.add_middleware(CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_KEY"]
)

class Inscription(BaseModel):
    nom: str
    prenom: str
    telephone: Optional[str] = None
    email: Optional[str] = None
    origine: Optional[str] = None
    est_interne: bool = True
    type_ticket: str
    evenement_id: Optional[str] = None
    operateur: Optional[str] = None
    code_transaction: Optional[str] = None
    montant: int = 0

class StatutUpdate(BaseModel):
    statut: str

import random, string, time

def gen_ticket(type_ticket):
    prefix = "SO" if type_ticket == "soiree" else "SW"
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    return f"{prefix}{code}"

@app.get("/api/inscriptions")
def get_inscriptions(limit: int = 500):
    res = supabase.table("inscriptions").select("*").order("created_at", desc=True).limit(limit).execute()
    return res.data

@app.post("/api/inscriptions")
def create_inscription(data: Inscription):
    ticket = gen_ticket(data.type_ticket)
    row = {**data.dict(), "numero_ticket": ticket, "statut": "pending", "billet_utilise": False}
    res = supabase.table("inscriptions").insert(row).execute()
    return {"numero_ticket": ticket, "data": res.data}

@app.patch("/api/inscriptions/{ticket}/statut")
def update_statut(ticket: str, update: StatutUpdate):
    res = supabase.table("inscriptions").update({"statut": update.statut}).eq("numero_ticket", ticket).execute()
    return res.data

@app.patch("/api/inscriptions/{ticket}/utilise")
def mark_used(ticket: str):
    res = supabase.table("inscriptions").update({"billet_utilise": True}).eq("numero_ticket", ticket).execute()
    return res.data

@app.get("/api/evenements")
def get_evenements():
    res = supabase.table("evenements").select("*").order("date_evt").order("heure_debut").execute()
    return res.data

@app.post("/api/evenements")
def upsert_evenement(data: dict):
    res = supabase.table("evenements").upsert(data).execute()
    return res.data

@app.get("/api/commentaires/{evenement_id}")
def get_commentaires(evenement_id: str):
    res = supabase.table("commentaires").select("*").eq("evenement_id", evenement_id).order("created_at", desc=True).execute()
    return res.data

@app.post("/api/commentaires")
def post_commentaire(data: dict):
    res = supabase.table("commentaires").insert(data).execute()
    return res.data