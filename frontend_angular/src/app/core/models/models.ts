// Interfaces miroir des serializers Django (gestion_scolaire/serializers.py)
// Un fichier unique pour rester simple à parcourir ; libre à toi de les
// éclater en plusieurs fichiers si le projet grossit.

export interface Paginated<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

export interface Utilisateur {
  id: number;
  id_ecole: number;
  nom: string;
  prenom: string;
  email: string;
  role: 'admin' | 'enseignant' | 'parent' | 'eleve' | 'caissier';
  telephone?: string;
  statut: 'actif' | 'inactif';
  date_creation: string;
}

export interface Ecole {
  id: number;
  nom: string;
  adresse?: string;
  telephone?: string;
  email?: string;
  plan_abonnement: 'essai' | 'standard' | 'premium';
  statut: 'actif' | 'suspendu' | 'resilie';
}

export interface AnneeScolaire {
  id: number;
  id_ecole: number;
  libelle: string;
  date_debut: string;
  date_fin: string;
  statut: 'active' | 'archivee';
}

export interface Niveau {
  id: number;
  id_ecole: number;
  libelle: string;
}

export interface Classe {
  id: number;
  id_ecole: number;
  id_annee_scolaire: number;
  id_niveau: number;
  niveau_libelle?: string;
  enseignant_nom?: string | null;
  nom: string;
  effectif: number;
  capacite_max: number;
  id_enseignant_principal?: number | null;
}

export interface ParentTuteur {
  id: number;
  id_ecole: number;
  nom: string;
  prenom: string;
  profession?: string;
  telephone?: string;
  email?: string;
}

export interface Eleve {
  id: number;
  id_ecole: number;
  matricule: string;
  nom: string;
  prenom: string;
  date_naissance?: string;
  sexe?: 'M' | 'F';
  adresse?: string;
  photo_url?: string;
  date_premiere_inscription?: string;
  statut: 'actif' | 'radie' | 'transfere' | 'diplome';
}

/** Une ligne du fichier importé, telle que le serveur l'a comprise. */
export interface LigneImport {
  source: string;
  nom: string;
  prenom: string;
  classe: string;
  telephone: string;
  sexe: string;
  mention: string;
  matricule: string;
  decision: 'creation' | 'doublon' | 'ignore';
  message: string;
}

export interface ClasseImport {
  nom: string;
  niveau: string;
  existe: boolean;
  eleves: number;
}

export interface ResultatImport {
  eleves_crees: number;
  classes_creees: string[];
  ignores: number;
  doublons: number;
}

/**
 * Réponse de /eleves/importer/ : identique en aperçu et après validation,
 * `valide` distinguant les deux — l'écran affiche le même tableau dans les
 * deux cas, ce qui évite de faire diverger ce qui est annoncé de ce qui est fait.
 */
export interface ApercuImport {
  fichier: string;
  annee_scolaire: string;
  total: number;
  a_creer: number;
  doublons: number;
  ignores: number;
  classes: ClasseImport[];
  eleves: LigneImport[];
  valide: boolean;
  resultat?: ResultatImport;
}

export interface EleveInscriptionPayload {
  id_ecole: number;
  nom: string;
  prenom: string;
  date_naissance?: string;
  sexe?: 'M' | 'F';
  adresse?: string;
  id_classe: number;
  id_annee_scolaire: number;
  date_inscription: string;
  parent_nom_complet?: string;
  parent_telephone?: string;
  parent_email?: string;
  parent_lien?: 'pere' | 'mere' | 'tuteur' | 'autre';
}

export interface Enseignant {
  id: number;
  id_ecole: number;
  nom?: string;
  prenom?: string;
  matricule: string;
  specialite?: string;
  date_embauche?: string;
  statut: 'actif' | 'inactif';
}

export interface Matiere {
  id: number;
  id_ecole: number;
  nom: string;
}

export interface Evaluation {
  id: number;
  id_matiere: number;
  id_classe: number;
  id_periode: number;
  type: 'devoir' | 'composition' | 'examen';
  date_evaluation: string;
  coefficient: number;
  /** Note maximale de l'épreuve. Les moyennes ramènent tout sur 20. */
  bareme: number;
}

export interface PeriodeScolaire {
  id: number;
  id_annee_scolaire: number;
  libelle: string;
  date_debut: string;
  date_fin: string;
}

export interface Note {
  id: number;
  id_eleve: number;
  id_evaluation: number;
  valeur: number;
  appreciation?: string;
  date_saisie: string;
}

export interface Bulletin {
  id: number;
  id_eleve: number;
  id_classe: number;
  id_periode: number;
  eleve_nom?: string;
  classe_nom?: string;
  periode_libelle?: string;
  moyenne_generale: number | null;
  rang_classe: number | null;
  appreciation_generale: string | null;
  date_generation: string;
  fichier_pdf_url?: string | null;
}

export interface Absence {
  id: number;
  id_eleve: number;
  id_classe: number;
  id_matiere?: number;
  date_absence: string;
  statut: 'absent' | 'retard';
  justifiee: boolean;
  motif?: string;
}

export interface Salle {
  id: number;
  id_ecole: number;
  nom: string;
  capacite?: number;
}

export interface EmploiDuTemps {
  id: number;
  id_classe: number;
  id_matiere: number;
  matiere_nom?: string;
  id_enseignant: number;
  enseignant_matricule?: string;
  id_salle?: number;
  salle_nom?: string;
  id_annee_scolaire: number;
  jour_semaine: 'lundi' | 'mardi' | 'mercredi' | 'jeudi' | 'vendredi' | 'samedi';
  heure_debut: string;
  heure_fin: string;
}

export interface TypeFrais {
  id: number;
  id_ecole: number;
  libelle: 'inscription' | 'mensualite' | 'demi_pension' | 'transport' | 'uniforme' | 'fournitures' | 'autre';
  montant_defaut: number;
  frequence?: string;
}

export interface FraisEleve {
  id: number;
  id_eleve: number;
  eleve_nom?: string;
  eleve_prenom?: string;
  id_type_frais: number;
  id_annee_scolaire: number;
  montant_du: number;
  montant_paye?: number;
  solde_restant?: number;
  echeance?: string;
  statut: 'paye' | 'partiel' | 'impaye';
}

export interface Paiement {
  id: number;
  id_frais_eleve: number;
  eleve_nom?: string;
  montant: number;
  date_paiement: string;
  methode: 'orange_money' | 'wave' | 'free_money' | 'carte_bancaire' | 'especes';
  reference_transaction?: string;
  /** Reçu généré automatiquement à l'encaissement. */
  id_recu?: number | null;
  numero_recu?: string | null;
}

export interface Recu {
  id: number;
  id_paiement: number;
  numero_recu: string;
  date_emission: string;
  fichier_pdf_url?: string;
}

export interface TypeDepense {
  id: number;
  id_ecole: number;
  libelle: string;
}

export interface Depense {
  id: number;
  id_type_depense: number;
  categorie?: string;
  id_ecole: number;
  motif: string;
  montant: number;
  date_depense: string;
  justificatif_url?: string;
}

export interface JournalCaisse {
  id: number;
  id_ecole: number;
  type_journal: 'caisse' | 'banque';
  journal?: string;
  date_mouvement: string;
  libelle: string;
  debit: number;
  credit: number;
  solde_apres?: number;
}

export interface Relance {
  id: number;
  id_frais_eleve: number;
  eleve_nom?: string;
  type_frais?: string;
  date_envoi: string;
  canal: 'sms' | 'email';
  statut: 'envoyee' | 'echouee';
}

export interface Budget {
  id: number;
  id_ecole: number;
  id_type_depense: number;
  categorie_depense?: string;
  id_annee_scolaire: number;
  categorie: string;
  montant_prevu: number;
  montant_realise: number;
  ecart?: number;
}

/** Soldes courants des journaux (GET /journal-caisse/soldes/). */
export interface Soldes {
  caisse: number;
  banque: number;
  total: number;
}

/** Aperçu ou résultat de POST /frais-eleves/generer/. */
export interface ResultatGenerationFrais {
  type_frais: string;
  classe: string;
  eleves: number;
  echeances: string[];
  montant: number;
  crees: number;
  ignores: number;
  total_genere: number;
  applique: boolean;
}

/** Aperçu ou résultat de POST /relances/generer/. */
export interface ResultatRelances {
  canal: string;
  relances: number;
  montant_relance: number;
  eleves: string[];
  applique: boolean;
}

export interface LigneVentilation {
  code?: string;
  libelle: string;
  montant: number;
  nombre: number;
}

export interface LigneBudget {
  categorie: string;
  type_depense: string;
  prevu: number;
  realise: number;
  ecart: number;
  consomme: number;
}

export interface LigneEvolution {
  mois: string;
  recettes: number;
  depenses: number;
}

/** Réponse de GET /finances/bilan/. */
export interface Bilan {
  periode: {
    id_annee_scolaire: number | null;
    annee: string | null;
    debut: string | null;
    fin: string | null;
  };
  recouvrement: { attendu: number; encaisse: number; reste: number; taux: number };
  impayes: { nombre: number; montant: number; echus: number; eleves_concernes: number };
  recettes: { total: number; par_type_frais: LigneVentilation[]; par_methode: LigneVentilation[] };
  depenses: { total: number; par_categorie: LigneVentilation[] };
  resultat: { recettes: number; depenses: number; solde: number };
  tresorerie: Soldes;
  budgets: LigneBudget[];
  evolution: LigneEvolution[];
}

// ---------------------------------------------------------------------------
// Gestion des élèves — dossier et mouvements
// ---------------------------------------------------------------------------

/** Réponse de GET /eleves/{id}/dossier/. */
export interface DossierEleve {
  eleve: {
    id: number;
    matricule: string;
    nom: string;
    prenom: string;
    sexe: string | null;
    date_naissance: string | null;
    adresse: string | null;
    photo_url: string | null;
    date_premiere_inscription: string | null;
    statut: 'actif' | 'radie' | 'transfere' | 'diplome';
    statut_libelle: string;
  };
  scolarite_courante: {
    id_inscription: number;
    classe: string;
    id_classe: number;
    annee: string;
    id_annee_scolaire: number;
    date_inscription: string;
    statut: string;
  } | null;
  parents: {
    id: number;
    nom_complet: string;
    lien: string;
    telephone: string | null;
    email: string | null;
    contact_principal: boolean;
  }[];
  inscriptions: {
    id: number;
    annee: string;
    classe: string;
    date_inscription: string;
    statut: string;
  }[];
  historique: {
    date: string;
    evenement: string;
    ancienne_classe: string | null;
    nouvelle_classe: string | null;
    motif: string | null;
    commentaire: string | null;
  }[];
  pieces: { id: number; type_piece: string; fichier_url: string; date_ajout: string }[];
  resultats: {
    periode: string;
    classe: string;
    moyenne_generale: number | null;
    rang_classe: number | null;
    appreciation: string | null;
  }[];
  finances: {
    total_du: number;
    total_paye: number;
    solde: number;
    frais_impayes: number;
  };
  assiduite: { absences: number; retards: number; non_justifiees: number };
}

/** Réponse de POST /inscriptions/reinscrire/. */
export interface ResultatReinscription {
  annee_source: string;
  annee_cible: string;
  classe_source: string;
  classe_cible: string;
  date_inscription: string;
  concernes: number;
  reinscrits: number;
  ignores: number;
  effectif_cible: number;
  avertissement: string | null;
  applique: boolean;
}

// ---------------------------------------------------------------------------
// Gestion pédagogique — notes, bulletins, palmarès
// ---------------------------------------------------------------------------

/** Réponse de GET /evaluations/{id}/grille/. */
export interface GrilleSaisie {
  id_evaluation: number;
  classe: string;
  id_classe: number;
  matiere: string;
  periode: string;
  type: string;
  date_evaluation: string;
  bareme: number;
  coefficient: number;
  eleves: LigneGrille[];
}

export interface LigneGrille {
  id_eleve: number;
  matricule: string;
  nom: string;
  prenom: string;
  valeur: number | null;
  appreciation: string | null;
}

export interface ResultatSaisieNotes {
  evaluation: number;
  classe: string;
  matiere: string;
  bareme: number;
  creees: number;
  modifiees: number;
  effacees: number;
}

export interface MoyenneMatiere {
  id_matiere: number;
  matiere: string;
  coefficient: number;
  nombre_notes: number;
  moyenne: number | null;
  appreciation: string | null;
}

export interface LigneMoyennes {
  id_eleve: number;
  matricule: string;
  nom: string;
  prenom: string;
  matieres: MoyenneMatiere[];
  moyenne_generale: number | null;
  appreciation: string | null;
  rang: number | null;
}

/** Réponse de POST /bulletins/generer/. */
export interface ResultatBulletins {
  classe: string;
  periode: string;
  eleves: number;
  bulletins: number;
  sans_note: number;
  crees: number;
  mis_a_jour: number;
  moyenne_classe: number | null;
  lignes: LigneMoyennes[];
  applique: boolean;
}

/** Réponse de GET /eleves/{id}/releve/. */
export interface ReleveNotes {
  eleve: { id: number; matricule: string; nom: string; prenom: string };
  classe: string;
  id_classe: number;
  periode: string;
  matieres: {
    id_matiere: number;
    matiere: string;
    coefficient: number;
    moyenne: number | null;
    appreciation: string | null;
    notes: {
      date: string;
      type: string;
      valeur: number;
      bareme: number;
      sur_20: number;
      coefficient: number;
      appreciation: string | null;
    }[];
  }[];
  moyenne_generale: number | null;
  appreciation: string | null;
  rang_classe: number | null;
  effectif: number;
}

export interface Palmares {
  classe: string;
  periode: string;
  lignes: {
    rang: number;
    matricule: string;
    nom: string;
    prenom: string;
    moyenne_generale: number;
    appreciation: string;
  }[];
}

export interface BlocStatistique {
  effectif: number;
  moyenne: number | null;
  minimum: number | null;
  maximum: number | null;
  reussite: number;
  taux_reussite: number | null;
}

export interface StatistiquesClasse {
  classe: string;
  periode: string;
  inscrits: number;
  sans_note: number;
  ensemble: BlocStatistique;
  repartition: Record<string, number>;
  matieres: (BlocStatistique & { matiere: string })[];
}

/** Réponse de GET /bulletins/synthese/ — résultats de toute l'école. */
export interface SyntheseEcole {
  periode: string | null;
  detail?: string;
  id_periode?: number;
  annee?: string;
  id_annee_scolaire?: number;
  ensemble?: BlocStatistique;
  repartition?: Record<string, number>;
  /** Classes classées par moyenne décroissante. */
  classes?: (BlocStatistique & {
    id_classe: number;
    classe: string;
    inscrits: number;
    sans_note: number;
  })[];
  /** Classes où rien n'a encore été saisi — distinctes des mauvais résultats. */
  classes_sans_notes?: { classe: string; id_classe: number; inscrits: number }[];
  /** Matières de la plus faible à la plus forte. */
  matieres?: (BlocStatistique & { matiere: string })[];
  evolution?: (BlocStatistique & { periode: string })[];
}
