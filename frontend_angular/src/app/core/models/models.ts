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
}

export interface Note {
  id: number;
  id_eleve: number;
  id_evaluation: number;
  valeur: number;
  appreciation?: string;
  date_saisie: string;
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
  montant: number;
  date_paiement: string;
  methode: 'orange_money' | 'wave' | 'free_money' | 'carte_bancaire' | 'especes';
  reference_transaction?: string;
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
  date_mouvement: string;
  libelle: string;
  debit: number;
  credit: number;
  solde_apres?: number;
}
