"""
API Backend — Clinique Aye de Tori
FastAPI + SQLAlchemy + PostgreSQL

Déploiement : Render.com (gratuit)
Start command : uvicorn main:app --host 0.0.0.0 --port $PORT
Variable d'environnement requise : DATABASE_URL
"""

import os
import hashlib
import json
from datetime import datetime, date
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Date,
    ForeignKey, text, func
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# ══════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════

DATABASE_URL = os.environ.get("DATABASE_URL", "")
# Render fournit parfois postgres:// — on corrige
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=3,
    max_overflow=2,
    pool_recycle=600,
    connect_args={"sslmode": "require", "connect_timeout": 10}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ══════════════════════════════════════════════════════
# MODÈLES SQLAlchemy (identiques à app.py Streamlit)
# ══════════════════════════════════════════════════════

class Utilisateur(Base):
    __tablename__ = "utilisateurs"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    nom         = Column(String(100), nullable=False)
    email       = Column(String(100), unique=True, nullable=False)
    mot_de_passe= Column(String(256), nullable=False)
    role        = Column(String(50), nullable=False, default="caissier")
    actif       = Column(Integer, default=1)
    permissions = Column(String(500), nullable=True)

class Assurance(Base):
    __tablename__ = "assurances"
    id    = Column(Integer, primary_key=True, autoincrement=True)
    nom   = Column(String(100), unique=True, nullable=False)
    email = Column(String(100))

class Facture(Base):
    __tablename__ = "factures"
    id                    = Column(Integer, primary_key=True, autoincrement=True)
    date_facture          = Column(Date, nullable=False, index=True)
    num_facture           = Column(String(50), unique=True, nullable=False)
    montant_total         = Column(Float, nullable=False)
    assurance_id          = Column(Integer, ForeignKey("assurances.id"), index=True)
    part_assureur         = Column(Float, default=0.0)
    part_assureur_payee   = Column(Float, default=0.0)
    part_assure           = Column(Float, default=0.0)
    statut_part_assure    = Column(String(50), default="Payée par Caisse")
    statut_part_assureur  = Column(String(50), default="En attente", index=True)
    date_depot            = Column(Date, nullable=True, index=True)
    motif_ecart_assurance = Column(String(255), nullable=True)

class Depense(Base):
    __tablename__ = "depenses"
    id            = Column(Integer, primary_key=True, autoincrement=True)
    date_depense  = Column(Date, nullable=False, index=True)
    categorie     = Column(String(100), nullable=False, index=True)
    description   = Column(String(255))
    montant       = Column(Float, nullable=False)
    mode_paiement = Column(String(50), default="Espèces")
    beneficiaire  = Column(String(150))
    user_nom      = Column(String(100))

class Produit(Base):
    __tablename__ = "produits"
    id           = Column(Integer, primary_key=True, autoincrement=True)
    code_article = Column(String(50), unique=True, nullable=False)
    designation  = Column(String(200), nullable=False)
    prix_unitaire= Column(Float, default=0.0)
    unite        = Column(String(50), default="unité")
    stock_initial= Column(Integer, default=0)
    seuil_alerte = Column(Integer, default=5)
    actif        = Column(Integer, default=1)

class MouvementStock(Base):
    __tablename__ = "mouvements_stock"
    id             = Column(Integer, primary_key=True, autoincrement=True)
    date_mouvement = Column(Date, nullable=False, index=True)
    produit_id     = Column(Integer, ForeignKey("produits.id"), index=True)
    type_mouvement = Column(String(10), nullable=False, index=True)
    quantite       = Column(Integer, nullable=False)
    motif          = Column(String(255))
    user_nom       = Column(String(100))

# Création des tables si elles n'existent pas
Base.metadata.create_all(bind=engine)


# ══════════════════════════════════════════════════════
# FASTAPI APP
# ══════════════════════════════════════════════════════

app = FastAPI(
    title="API Clinique Aye de Tori",
    description="Backend de gestion financière et médicale",
    version="2.0"
)

# CORS — autorise GitHub Pages (et tous origines en dev)
ALLOWED_ORIGINS = os.environ.get(
    "ALLOWED_ORIGINS",
    "*"   # Remplacer par "https://votrenom.github.io" en production
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Dépendance DB ──
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ══════════════════════════════════════════════════════
# UTILITAIRES
# ══════════════════════════════════════════════════════

def hasher_mdp(mdp: str) -> str:
    return hashlib.sha256(mdp.encode()).hexdigest()

def lire_permissions(permissions_json: str):
    if not permissions_json:
        return set()
    try:
        return set(json.loads(permissions_json))
    except Exception:
        return set()

def suivi_echeance(date_depot, statut: str) -> str:
    if statut in ("Soldé", "Rejeté", "N/A"):
        return statut
    if not date_depot:
        return "⏸ Pas encore déposé"
    jours = (date.today() - date_depot).days
    if jours > 30:
        return f"🚨 À RELANCER ({jours} j)"
    elif jours >= 25:
        return f"⚠️ Échéance proche ({jours} j)"
    return f"🕐 En attente ({jours} j)"


# ══════════════════════════════════════════════════════
# SCHÉMAS PYDANTIC
# ══════════════════════════════════════════════════════

class LoginSchema(BaseModel):
    email: str
    mot_de_passe: str

class FactureCreateSchema(BaseModel):
    num_facture: str
    date_facture: date
    montant_total: float
    part_assure: float
    assurance_nom: str

class FactureUpdateSchema(BaseModel):
    num_facture:          Optional[str]   = None
    date_facture:         Optional[date]  = None
    date_depot:           Optional[date]  = None
    assurance_nom:        Optional[str]   = None
    montant_total:        Optional[float] = None
    part_assure:          Optional[float] = None
    part_assureur_payee:  Optional[float] = None
    motif_ecart_assurance:Optional[str]   = None

class DepenseCreateSchema(BaseModel):
    date_depense:  date
    categorie:     str
    description:   Optional[str] = ""
    montant:       float
    mode_paiement: Optional[str] = "Espèces"
    beneficiaire:  Optional[str] = ""
    user_nom:      Optional[str] = ""

class AssuranceCreateSchema(BaseModel):
    nom:   str
    email: Optional[str] = ""

class ProduitCreateSchema(BaseModel):
    code_article:  str
    designation:   str
    prix_unitaire: float = 0.0
    unite:         str   = "unité"
    stock_initial: int   = 0
    seuil_alerte:  int   = 5

class MouvementSchema(BaseModel):
    date_mouvement: date
    produit_id:     int
    type_mouvement: str   # "entree" ou "sortie"
    quantite:       int
    motif:          Optional[str] = ""
    user_nom:       Optional[str] = ""

class EncaissementSchema(BaseModel):
    num_facture:   str
    montant_recu:  float
    motif:         Optional[str] = ""

class DateDepotSchema(BaseModel):
    num_factures: List[str]
    date_depot:   date

class UtilisateurCreateSchema(BaseModel):
    nom:         str
    email:       str
    mot_de_passe:str
    role:        str         = "caissier"
    permissions: List[str]  = []

class UtilisateurUpdateSchema(BaseModel):
    nom:         Optional[str]       = None
    role:        Optional[str]       = None
    permissions: Optional[List[str]] = None
    actif:       Optional[int]       = None
    mot_de_passe:Optional[str]       = None


# ══════════════════════════════════════════════════════
# 1. AUTHENTIFICATION
# ══════════════════════════════════════════════════════

@app.post("/auth/login")
def login(body: LoginSchema, db: Session = Depends(get_db)):
    """Vérifie email + mot de passe. Retourne les infos utilisateur."""
    user = db.query(Utilisateur).filter_by(
        email=body.email,
        mot_de_passe=hasher_mdp(body.mot_de_passe),
        actif=1
    ).first()
    if not user:
        raise HTTPException(status_code=401, detail="Identifiants incorrects ou compte inactif.")
    perms = list(lire_permissions(user.permissions)) if user.role != "admin" else [
        "corriger_factures","exporter","gerer_assurances","gerer_depots",
        "encaisser_virements","voir_finances","voir_analyses",
        "gerer_utilisateurs","gerer_depenses"
    ]
    return {
        "id":          user.id,
        "nom":         user.nom,
        "email":       user.email,
        "role":        user.role,
        "permissions": perms
    }


# ══════════════════════════════════════════════════════
# 2. DASHBOARD — KPIs GLOBAUX
# ══════════════════════════════════════════════════════

@app.get("/dashboard/kpis")
def get_kpis(
    annee: Optional[int] = None,
    mois:  Optional[int] = None,
    db:    Session = Depends(get_db)
):
    """Retourne tous les indicateurs clés pour la page d'accueil."""
    # ── Filtres factures ──
    q_fac = db.query(Facture)
    if annee:
        q_fac = q_fac.filter(func.extract("year",  Facture.date_facture) == annee)
    if mois:
        q_fac = q_fac.filter(func.extract("month", Facture.date_facture) == mois)
    factures = q_fac.all()

    ca_total        = sum(f.montant_total        for f in factures)
    total_encaisse  = sum(f.part_assure + f.part_assureur_payee for f in factures)
    total_assureur  = sum(f.part_assureur        for f in factures)
    total_ass_paye  = sum(f.part_assureur_payee  for f in factures)
    reste_assureurs = total_assureur - total_ass_paye

    # Retards (dépôt > 30 jours et encore en attente)
    retards = [
        f for f in factures
        if f.date_depot
        and f.statut_part_assureur == "En attente"
        and (date.today() - f.date_depot).days > 30
    ]

    # ── Filtres dépenses ──
    q_dep = db.query(Depense)
    if annee:
        q_dep = q_dep.filter(func.extract("year",  Depense.date_depense) == annee)
    if mois:
        q_dep = q_dep.filter(func.extract("month", Depense.date_depense) == mois)
    depenses = q_dep.all()
    total_depenses = sum(d.montant for d in depenses)

    benefice_net = total_encaisse - total_depenses
    benefice_ca  = ca_total       - total_depenses

    taux_recouvrement = round(total_encaisse / ca_total * 100, 1) if ca_total else 0

    return {
        "ca_total":           ca_total,
        "total_encaisse":     total_encaisse,
        "total_depenses":     total_depenses,
        "benefice_net":       benefice_net,
        "benefice_ca":        benefice_ca,
        "reste_assureurs":    reste_assureurs,
        "total_ass_paye":     total_ass_paye,
        "taux_recouvrement":  taux_recouvrement,
        "nb_factures":        len(factures),
        "nb_retards":         len(retards),
        "montant_retards":    sum(f.part_assureur for f in retards),
    }


@app.get("/dashboard/synthese")
def get_synthese(
    annee: Optional[int] = None,
    mois:  Optional[int] = None,
    db:    Session = Depends(get_db)
):
    """Synthèse financière ligne par ligne (tableau récapitulatif)."""
    q_fac = db.query(Facture)
    q_dep = db.query(Depense)
    if annee:
        q_fac = q_fac.filter(func.extract("year",  Facture.date_facture) == annee)
        q_dep = q_dep.filter(func.extract("year",  Depense.date_depense) == annee)
    if mois:
        q_fac = q_fac.filter(func.extract("month", Facture.date_facture) == mois)
        q_dep = q_dep.filter(func.extract("month", Depense.date_depense) == mois)

    factures = q_fac.all()
    depenses = q_dep.all()

    ca             = sum(f.montant_total       for f in factures)
    part_caisse    = sum(f.part_assure         for f in factures)
    part_assureur  = sum(f.part_assureur       for f in factures)
    ass_payee      = sum(f.part_assureur_payee for f in factures)
    reste_ass      = part_assureur - ass_payee
    encaisse       = part_caisse + ass_payee
    total_dep      = sum(d.montant             for d in depenses)
    benefice_net   = encaisse - total_dep
    benefice_ca    = ca       - total_dep

    return {
        "ca_total":        ca,
        "part_caisse":     part_caisse,
        "part_assureur":   part_assureur,
        "ass_deja_percue": ass_payee,
        "reste_percevoir": reste_ass,
        "total_encaisse":  encaisse,
        "total_depenses":  total_dep,
        "benefice_net":    benefice_net,
        "benefice_ca":     benefice_ca,
    }


@app.get("/dashboard/depenses-par-categorie")
def depenses_par_categorie(
    annee: Optional[int] = None,
    mois:  Optional[int] = None,
    db:    Session = Depends(get_db)
):
    """Totaux dépenses groupés par catégorie (pour le graphe donut/barres)."""
    q = db.query(
        Depense.categorie,
        func.sum(Depense.montant).label("total")
    ).group_by(Depense.categorie)
    if annee:
        q = q.filter(func.extract("year",  Depense.date_depense) == annee)
    if mois:
        q = q.filter(func.extract("month", Depense.date_depense) == mois)
    rows = q.order_by(func.sum(Depense.montant).desc()).all()
    total_all = sum(r.total for r in rows) or 1
    return [
        {"categorie": r.categorie, "montant": r.total,
         "pct": round(r.total / total_all * 100, 1)}
        for r in rows
    ]


@app.get("/dashboard/evolution-mensuelle")
def evolution_mensuelle(
    annee: Optional[int] = None,
    db:    Session = Depends(get_db)
):
    """CA facturé, encaissé et dépenses mois par mois."""
    q_fac = db.query(
        func.extract("year",  Facture.date_facture).label("annee"),
        func.extract("month", Facture.date_facture).label("mois"),
        func.sum(Facture.montant_total).label("ca"),
        func.sum(Facture.part_assure + Facture.part_assureur_payee).label("encaisse")
    ).group_by("annee", "mois")

    q_dep = db.query(
        func.extract("year",  Depense.date_depense).label("annee"),
        func.extract("month", Depense.date_depense).label("mois"),
        func.sum(Depense.montant).label("depenses")
    ).group_by("annee", "mois")

    if annee:
        q_fac = q_fac.filter(func.extract("year", Facture.date_facture) == annee)
        q_dep = q_dep.filter(func.extract("year", Depense.date_depense) == annee)

    fac_rows = {(int(r.annee), int(r.mois)): {"ca": r.ca, "encaisse": r.encaisse}
                for r in q_fac.all()}
    dep_rows = {(int(r.annee), int(r.mois)): r.depenses
                for r in q_dep.all()}

    all_keys = sorted(set(list(fac_rows.keys()) + list(dep_rows.keys())))
    mois_labels = ["","Jan","Fév","Mar","Avr","Mai","Jun",
                   "Jul","Aoû","Sep","Oct","Nov","Déc"]
    result = []
    for (an, mo) in all_keys:
        fr = fac_rows.get((an, mo), {"ca": 0, "encaisse": 0})
        dep = dep_rows.get((an, mo), 0)
        enc = fr["encaisse"] or 0
        ben = enc - (dep or 0)
        result.append({
            "periode":    f"{mois_labels[mo]} {an}",
            "annee":      an,
            "mois":       mo,
            "ca":         fr["ca"] or 0,
            "encaisse":   enc,
            "depenses":   dep or 0,
            "benefice":   ben,
        })
    return result


# ══════════════════════════════════════════════════════
# 3. FACTURES
# ══════════════════════════════════════════════════════

def _facture_to_dict(f: Facture, assurance_nom: str) -> dict:
    dep = f.date_depot
    return {
        "id":                    f.id,
        "num_facture":           f.num_facture,
        "date_facture":          str(f.date_facture),
        "structure_assurance":   assurance_nom,
        "montant_total":         f.montant_total,
        "part_assure":           f.part_assure,
        "part_assureur":         f.part_assureur,
        "part_assureur_payee":   f.part_assureur_payee,
        "statut_part_assureur":  f.statut_part_assureur,
        "date_depot":            str(dep) if dep else None,
        "motif_ecart_assurance": f.motif_ecart_assurance,
        "montant_encaisse":      f.part_assure + f.part_assureur_payee,
        "reste_recouvrer":       f.part_assureur - f.part_assureur_payee,
        "suivi_echeance":        suivi_echeance(dep, f.statut_part_assureur),
    }

@app.get("/factures")
def get_factures(
    annee:       Optional[int] = None,
    mois:        Optional[int] = None,
    statut:      Optional[str] = None,
    assurance:   Optional[str] = None,
    num_facture: Optional[str] = None,
    limit:       int = 500,
    db:          Session = Depends(get_db)
):
    """Liste des factures avec filtres."""
    q = db.query(Facture, Assurance.nom.label("assurance_nom"))\
          .outerjoin(Assurance, Facture.assurance_id == Assurance.id)
    if annee:
        q = q.filter(func.extract("year",  Facture.date_facture) == annee)
    if mois:
        q = q.filter(func.extract("month", Facture.date_facture) == mois)
    if statut:
        q = q.filter(Facture.statut_part_assureur == statut)
    if assurance:
        q = q.filter(Assurance.nom.ilike(f"%{assurance}%"))
    if num_facture:
        q = q.filter(Facture.num_facture.ilike(f"%{num_facture}%"))
    rows = q.order_by(Facture.date_facture.desc()).limit(limit).all()
    return [_facture_to_dict(f, nom or "SANS ASSURANCE") for f, nom in rows]


@app.post("/factures", status_code=201)
def creer_facture(body: FactureCreateSchema, db: Session = Depends(get_db)):
    """Enregistre une nouvelle facture."""
    if db.query(Facture).filter_by(num_facture=body.num_facture).first():
        raise HTTPException(400, f"Le numéro {body.num_facture} existe déjà.")

    nom_ass = body.assurance_nom.strip().upper() or "SANS ASSURANCE"
    assur = db.query(Assurance).filter_by(nom=nom_ass).first()
    if not assur:
        assur = Assurance(nom=nom_ass, email="")
        db.add(assur); db.flush()

    p_assureur = max(0.0, body.montant_total - body.part_assure) \
                 if nom_ass != "SANS ASSURANCE" else 0.0
    statut = "En attente" if p_assureur > 0 else "N/A"

    fac = Facture(
        date_facture  = body.date_facture,
        num_facture   = body.num_facture,
        montant_total = body.montant_total,
        assurance_id  = assur.id,
        part_assure   = body.part_assure,
        part_assureur = p_assureur,
        statut_part_assureur = statut
    )
    db.add(fac); db.commit(); db.refresh(fac)
    return {"message": "Facture enregistrée.", "id": fac.id}


@app.put("/factures/{num_facture}")
def modifier_facture(
    num_facture: str,
    body: FactureUpdateSchema,
    db:   Session = Depends(get_db)
):
    """Modifie une facture existante (corrections)."""
    fac = db.query(Facture).filter_by(num_facture=num_facture).first()
    if not fac:
        raise HTTPException(404, "Facture introuvable.")

    if body.num_facture   is not None: fac.num_facture   = body.num_facture
    if body.date_facture  is not None: fac.date_facture  = body.date_facture
    if body.date_depot    is not None: fac.date_depot    = body.date_depot
    if body.montant_total is not None: fac.montant_total = body.montant_total
    if body.part_assure   is not None: fac.part_assure   = body.part_assure

    if body.assurance_nom is not None:
        nom_ass = body.assurance_nom.strip().upper()
        assur = db.query(Assurance).filter_by(nom=nom_ass).first()
        if not assur:
            assur = Assurance(nom=nom_ass, email="")
            db.add(assur); db.flush()
        fac.assurance_id  = assur.id
        fac.part_assureur = max(0.0, fac.montant_total - fac.part_assure) \
                            if nom_ass != "SANS ASSURANCE" else 0.0

    if body.part_assureur_payee is not None:
        paye = body.part_assureur_payee
        fac.part_assureur_payee = paye
        if fac.part_assureur == 0:
            fac.statut_part_assureur = "N/A"
        elif paye == 0:
            fac.statut_part_assureur = "En attente"
        elif paye >= fac.part_assureur:
            fac.statut_part_assureur = "Soldé"
        else:
            fac.statut_part_assureur = "Payé Partiel"

    if body.motif_ecart_assurance is not None:
        fac.motif_ecart_assurance = body.motif_ecart_assurance

    db.commit()
    return {"message": "Facture mise à jour."}


@app.delete("/factures/{num_facture}")
def supprimer_facture(num_facture: str, db: Session = Depends(get_db)):
    fac = db.query(Facture).filter_by(num_facture=num_facture).first()
    if not fac:
        raise HTTPException(404, "Facture introuvable.")
    db.delete(fac); db.commit()
    return {"message": "Facture supprimée."}


# ── Dépôt global ──
@app.post("/factures/depot-global")
def enregistrer_depot(body: DateDepotSchema, db: Session = Depends(get_db)):
    """Affecte une date de dépôt à une liste de factures."""
    updated = 0
    for num in body.num_factures:
        fac = db.query(Facture).filter_by(num_facture=num).first()
        if fac:
            fac.date_depot = body.date_depot
            updated += 1
    db.commit()
    return {"message": f"{updated} facture(s) mise(s) à jour."}


# ── Encaissements virement ──
@app.post("/factures/encaissements")
def encaisser_virements(
    body: List[EncaissementSchema],
    db:   Session = Depends(get_db)
):
    """Enregistre les encaissements banque pour une liste de factures."""
    updated = 0
    for enc in body:
        fac = db.query(Facture).filter_by(num_facture=enc.num_facture).first()
        if not fac:
            continue
        fac.part_assureur_payee = enc.montant_recu
        if enc.montant_recu == 0:
            fac.statut_part_assureur   = "Rejeté"
            fac.motif_ecart_assurance  = enc.motif
        elif enc.montant_recu >= fac.part_assureur:
            fac.statut_part_assureur   = "Soldé"
            fac.motif_ecart_assurance  = None
        else:
            fac.statut_part_assureur   = "Payé Partiel"
            fac.motif_ecart_assurance  = enc.motif
        updated += 1
    db.commit()
    return {"message": f"{updated} encaissement(s) enregistré(s)."}


# ── Retards ──
@app.get("/factures/retards")
def get_retards(db: Session = Depends(get_db)):
    """Factures en attente depuis plus de 30 jours après dépôt."""
    rows = db.query(Facture, Assurance.nom.label("assurance_nom"))\
             .outerjoin(Assurance, Facture.assurance_id == Assurance.id)\
             .filter(
                 Facture.statut_part_assureur == "En attente",
                 Facture.date_depot != None,
                 text("factures.date_depot < CURRENT_DATE - INTERVAL '30 days'")
             ).order_by(Facture.date_depot).all()
    result = []
    for f, nom in rows:
        d = _facture_to_dict(f, nom or "SANS ASSURANCE")
        d["jours_retard"] = (date.today() - f.date_depot).days
        result.append(d)
    return result


# ── Situation par compagnie ──
@app.get("/factures/situation-compagnies")
def situation_compagnies(
    annee: Optional[int] = None,
    mois:  Optional[int] = None,
    db:    Session = Depends(get_db)
):
    q = db.query(
        Assurance.nom,
        func.sum(Facture.montant_total).label("ca"),
        func.sum(Facture.part_assure + Facture.part_assureur_payee).label("encaisse"),
        func.sum(Facture.part_assureur - Facture.part_assureur_payee).label("reste"),
        func.count(Facture.id).label("nb_factures")
    ).outerjoin(Facture, Assurance.id == Facture.assurance_id)\
     .group_by(Assurance.nom)
    if annee:
        q = q.filter(func.extract("year",  Facture.date_facture) == annee)
    if mois:
        q = q.filter(func.extract("month", Facture.date_facture) == mois)
    rows = q.order_by(func.sum(Facture.montant_total).desc()).all()
    return [{
        "compagnie":    r.nom,
        "ca":           r.ca or 0,
        "encaisse":     r.encaisse or 0,
        "reste":        r.reste or 0,
        "nb_factures":  r.nb_factures,
        "taux":         round((r.encaisse or 0) / (r.ca or 1) * 100, 1)
    } for r in rows]


# ══════════════════════════════════════════════════════
# 4. DÉPENSES
# ══════════════════════════════════════════════════════

@app.get("/depenses")
def get_depenses(
    annee:     Optional[int] = None,
    mois:      Optional[int] = None,
    categorie: Optional[str] = None,
    limit:     int = 500,
    db:        Session = Depends(get_db)
):
    q = db.query(Depense)
    if annee:     q = q.filter(func.extract("year",  Depense.date_depense) == annee)
    if mois:      q = q.filter(func.extract("month", Depense.date_depense) == mois)
    if categorie: q = q.filter(Depense.categorie == categorie)
    rows = q.order_by(Depense.date_depense.desc()).limit(limit).all()
    return [{
        "id":            d.id,
        "date_depense":  str(d.date_depense),
        "categorie":     d.categorie,
        "description":   d.description or "",
        "montant":       d.montant,
        "mode_paiement": d.mode_paiement or "Espèces",
        "beneficiaire":  d.beneficiaire or "",
        "user_nom":      d.user_nom or "",
    } for d in rows]


@app.post("/depenses", status_code=201)
def creer_depense(body: DepenseCreateSchema, db: Session = Depends(get_db)):
    dep = Depense(**body.dict())
    db.add(dep); db.commit(); db.refresh(dep)
    return {"message": "Dépense enregistrée.", "id": dep.id}


@app.delete("/depenses/{depense_id}")
def supprimer_depense(depense_id: int, db: Session = Depends(get_db)):
    dep = db.query(Depense).filter_by(id=depense_id).first()
    if not dep:
        raise HTTPException(404, "Dépense introuvable.")
    db.delete(dep); db.commit()
    return {"message": "Dépense supprimée."}


@app.delete("/depenses/categorie/{categorie}")
def supprimer_depenses_categorie(categorie: str, db: Session = Depends(get_db)):
    nb = db.query(Depense).filter_by(categorie=categorie).delete()
    db.commit()
    return {"message": f"{nb} dépense(s) supprimée(s)."}


# ══════════════════════════════════════════════════════
# 5. ASSURANCES
# ══════════════════════════════════════════════════════

@app.get("/assurances")
def get_assurances(db: Session = Depends(get_db)):
    rows = db.query(Assurance).order_by(Assurance.nom).all()
    return [{"id": a.id, "nom": a.nom, "email": a.email or ""} for a in rows]


@app.post("/assurances", status_code=201)
def creer_assurance(body: AssuranceCreateSchema, db: Session = Depends(get_db)):
    if db.query(Assurance).filter_by(nom=body.nom.upper()).first():
        raise HTTPException(400, "Cette compagnie existe déjà.")
    a = Assurance(nom=body.nom.upper(), email=body.email)
    db.add(a); db.commit(); db.refresh(a)
    return {"message": "Assurance créée.", "id": a.id}


@app.delete("/assurances/{assurance_id}")
def supprimer_assurance(assurance_id: int, db: Session = Depends(get_db)):
    a = db.query(Assurance).filter_by(id=assurance_id).first()
    if not a:
        raise HTTPException(404, "Assurance introuvable.")
    db.delete(a); db.commit()
    return {"message": "Assurance supprimée."}


# ══════════════════════════════════════════════════════
# 6. STOCK — PRODUITS
# ══════════════════════════════════════════════════════

@app.get("/stock/produits")
def get_produits(db: Session = Depends(get_db)):
    """Retourne tous les produits avec leur stock calculé."""
    produits = db.query(Produit).filter_by(actif=1).all()
    result = []
    for p in produits:
        entrees = db.query(func.coalesce(func.sum(MouvementStock.quantite), 0))\
                    .filter_by(produit_id=p.id, type_mouvement="entree").scalar() or 0
        sorties = db.query(func.coalesce(func.sum(MouvementStock.quantite), 0))\
                    .filter_by(produit_id=p.id, type_mouvement="sortie").scalar() or 0
        stock_final = p.stock_initial + entrees - sorties
        if stock_final <= 0:
            statut = "🔴 RUPTURE"
        elif stock_final <= p.seuil_alerte:
            statut = "🟠 ALERTE"
        else:
            statut = "🟢 OK"
        result.append({
            "id":            p.id,
            "code_article":  p.code_article,
            "designation":   p.designation,
            "prix_unitaire": p.prix_unitaire,
            "unite":         p.unite,
            "stock_initial": p.stock_initial,
            "entrees":       entrees,
            "sorties":       sorties,
            "stock_final":   stock_final,
            "seuil_alerte":  p.seuil_alerte,
            "valeur":        stock_final * p.prix_unitaire,
            "statut":        statut,
        })
    return result


@app.get("/stock/dashboard")
def get_stock_dashboard(db: Session = Depends(get_db)):
    """KPIs stock : ruptures, alertes, valeur totale."""
    produits = db.query(Produit).filter_by(actif=1).all()
    ok = alertes = ruptures = 0
    valeur_totale = 0.0
    for p in produits:
        entrees = db.query(func.coalesce(func.sum(MouvementStock.quantite), 0))\
                    .filter_by(produit_id=p.id, type_mouvement="entree").scalar() or 0
        sorties = db.query(func.coalesce(func.sum(MouvementStock.quantite), 0))\
                    .filter_by(produit_id=p.id, type_mouvement="sortie").scalar() or 0
        sf = p.stock_initial + entrees - sorties
        valeur_totale += sf * p.prix_unitaire
        if sf <= 0:            ruptures += 1
        elif sf <= p.seuil_alerte: alertes  += 1
        else:                      ok       += 1
    return {
        "total_produits": len(produits),
        "ok":             ok,
        "alertes":        alertes,
        "ruptures":       ruptures,
        "valeur_totale":  valeur_totale,
    }


@app.post("/stock/produits", status_code=201)
def creer_produit(body: ProduitCreateSchema, db: Session = Depends(get_db)):
    if db.query(Produit).filter_by(code_article=body.code_article).first():
        raise HTTPException(400, "Code article déjà utilisé.")
    p = Produit(**body.dict())
    db.add(p); db.commit(); db.refresh(p)
    return {"message": "Produit créé.", "id": p.id}


@app.delete("/stock/produits/{produit_id}")
def desactiver_produit(produit_id: int, db: Session = Depends(get_db)):
    p = db.query(Produit).filter_by(id=produit_id).first()
    if not p:
        raise HTTPException(404, "Produit introuvable.")
    p.actif = 0; db.commit()
    return {"message": "Produit désactivé."}


# ── Mouvements stock ──
@app.get("/stock/mouvements")
def get_mouvements(
    produit_id: Optional[int] = None,
    type_mouvement: Optional[str] = None,
    limit: int = 200,
    db: Session = Depends(get_db)
):
    q = db.query(MouvementStock, Produit.designation.label("produit_nom"))\
          .outerjoin(Produit, MouvementStock.produit_id == Produit.id)
    if produit_id:      q = q.filter(MouvementStock.produit_id == produit_id)
    if type_mouvement:  q = q.filter(MouvementStock.type_mouvement == type_mouvement)
    rows = q.order_by(MouvementStock.date_mouvement.desc()).limit(limit).all()
    return [{
        "id":             m.id,
        "date_mouvement": str(m.date_mouvement),
        "produit_id":     m.produit_id,
        "produit_nom":    nom or "",
        "type_mouvement": m.type_mouvement,
        "quantite":       m.quantite,
        "motif":          m.motif or "",
        "user_nom":       m.user_nom or "",
    } for m, nom in rows]


@app.post("/stock/mouvements", status_code=201)
def creer_mouvement(body: MouvementSchema, db: Session = Depends(get_db)):
    produit = db.query(Produit).filter_by(id=body.produit_id, actif=1).first()
    if not produit:
        raise HTTPException(404, "Produit introuvable ou inactif.")
    if body.type_mouvement not in ("entree", "sortie"):
        raise HTTPException(400, "type_mouvement doit être 'entree' ou 'sortie'.")
    mv = MouvementStock(**body.dict())
    db.add(mv); db.commit(); db.refresh(mv)
    return {"message": "Mouvement enregistré.", "id": mv.id}


# ══════════════════════════════════════════════════════
# 7. UTILISATEURS
# ══════════════════════════════════════════════════════

TOUTES_PERMISSIONS = [
    "corriger_factures","exporter","gerer_assurances","gerer_depots",
    "encaisser_virements","voir_finances","voir_analyses",
    "gerer_utilisateurs","gerer_depenses",
]

@app.get("/utilisateurs")
def get_utilisateurs(db: Session = Depends(get_db)):
    rows = db.query(Utilisateur).all()
    return [{
        "id":          u.id,
        "nom":         u.nom,
        "email":       u.email,
        "role":        u.role,
        "actif":       u.actif,
        "permissions": list(lire_permissions(u.permissions))
                       if u.role != "admin" else TOUTES_PERMISSIONS
    } for u in rows]


@app.post("/utilisateurs", status_code=201)
def creer_utilisateur(body: UtilisateurCreateSchema, db: Session = Depends(get_db)):
    if db.query(Utilisateur).filter_by(email=body.email).first():
        raise HTTPException(400, "Email déjà utilisé.")
    u = Utilisateur(
        nom          = body.nom,
        email        = body.email,
        mot_de_passe = hasher_mdp(body.mot_de_passe),
        role         = body.role,
        permissions  = json.dumps(body.permissions)
    )
    db.add(u); db.commit(); db.refresh(u)
    return {"message": "Utilisateur créé.", "id": u.id}


@app.put("/utilisateurs/{utilisateur_id}")
def modifier_utilisateur(
    utilisateur_id: int,
    body: UtilisateurUpdateSchema,
    db:   Session = Depends(get_db)
):
    u = db.query(Utilisateur).filter_by(id=utilisateur_id).first()
    if not u:
        raise HTTPException(404, "Utilisateur introuvable.")
    if body.nom         is not None: u.nom         = body.nom
    if body.role        is not None: u.role        = body.role
    if body.actif       is not None: u.actif       = body.actif
    if body.permissions is not None: u.permissions = json.dumps(body.permissions)
    if body.mot_de_passe is not None:
        u.mot_de_passe = hasher_mdp(body.mot_de_passe)
    db.commit()
    return {"message": "Utilisateur mis à jour."}


@app.delete("/utilisateurs/{utilisateur_id}")
def supprimer_utilisateur(utilisateur_id: int, db: Session = Depends(get_db)):
    u = db.query(Utilisateur).filter_by(id=utilisateur_id).first()
    if not u:
        raise HTTPException(404, "Utilisateur introuvable.")
    db.delete(u); db.commit()
    return {"message": "Utilisateur supprimé."}


# ══════════════════════════════════════════════════════
# 8. RELANCES (données pour génération lettre côté client)
# ══════════════════════════════════════════════════════

@app.get("/relances/{assurance_nom}")
def get_relances_assurance(assurance_nom: str, db: Session = Depends(get_db)):
    """Retourne les factures en retard pour une compagnie donnée."""
    assur = db.query(Assurance).filter(
        Assurance.nom.ilike(assurance_nom)
    ).first()
    if not assur:
        raise HTTPException(404, "Compagnie introuvable.")
    rows = db.query(Facture).filter(
        Facture.assurance_id == assur.id,
        Facture.statut_part_assureur == "En attente",
        Facture.date_depot != None,
        text("factures.date_depot < CURRENT_DATE - INTERVAL '30 days'")
    ).order_by(Facture.date_depot).all()
    return {
        "compagnie": {"nom": assur.nom, "email": assur.email or ""},
        "factures": [{
            "num_facture":   f.num_facture,
            "date_facture":  str(f.date_facture),
            "date_depot":    str(f.date_depot),
            "part_assureur": f.part_assureur,
            "jours_retard":  (date.today() - f.date_depot).days,
        } for f in rows],
        "montant_total": sum(f.part_assureur for f in rows),
    }


# ══════════════════════════════════════════════════════
# 9. SANTÉ API
# ══════════════════════════════════════════════════════

@app.get("/")
def health():
    return {
        "status":  "ok",
        "app":     "Clinique Aye de Tori — API v2.0",
        "date":    str(date.today()),
    }
