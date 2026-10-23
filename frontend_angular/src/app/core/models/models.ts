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
  /** `null` pour le seul super administrateur, qui n'appartient à aucune école. */
  id_ecole: number | null;
  nom: string;
  prenom: string;
  email: string;
  role: Role;
  telephone?: string;
  statut: 'actif' | 'inactif';
  date_creation: string;
  /** Renseignés par /auth/moi/ seulement. */
  role_libelle?: string;
  profil?: string;
  /** Nom de l'école du compte, affiché dans le menu ; `null` pour le super administrateur. */
  ecole_nom?: string | null;
  /** Modules ouverts au compte et niveau d'accès, lus sur la matrice du serveur. */
  acces?: Partial<Record<Module, NiveauAcces>>;
}

export type Role =
  | 'super_admin'
  | 'admin' | 'directeur' | 'responsable_administratif'
  | 'surveillant' | 'secretaire'
  | 'caissier' | 'comptable' | 'responsable_financier'
  | 'enseignant' | 'parent' | 'eleve';

export type NiveauAcces = 'lecture' | 'ecriture';

/** Les modules de la matrice des autorisations (backend : permissions.py). */
export type Module =
  | 'tableau_de_bord' | 'eleves' | 'inscriptions' | 'parents' | 'classes'
  | 'matieres' | 'emploi_du_temps' | 'presences' | 'notes' | 'bulletins'
  | 'personnel' | 'frais' | 'encaissement' | 'relances' | 'journal_caisse'
  | 'depenses' | 'types_depenses' | 'budget' | 'types_frais' | 'parametres' | 'sante'
  | 'annees_scolaires' | 'comptes' | 'communications' | 'documents';

export interface Ecole {
  id: number;
  nom: string;
  /** Ouvre le matricule des élèves ; attribué par la plateforme. */
  code_ecole: string;
  adresse?: string;
  telephone?: string;
  email?: string;
  logo_url?: string;
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
  /** Montant de chaque frais pour ce niveau, par identifiant de type de frais. */
  tarifs?: Record<string, number>;
  /** Fourchette d'âge admise (années révolues à la date de référence). */
  age_min?: number | null;
  age_max?: number | null;
}

export interface Classe {
  id: number;
  id_ecole: number;
  id_annee_scolaire: number;
  id_niveau: number | null;
  niveau_libelle?: string | null;
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
  adresse?: string;
  /** Enfants rattachés, frères et sœurs compris (lecture seule). */
  enfants?: EnfantDuParent[];
  /** Leurs noms, séparés par des virgules, pour la liste des parents. */
  enfants_noms?: string;
}

/** Un enfant vu depuis la fiche de son parent. */
export interface EnfantDuParent {
  /** Identifiant du rattachement (`/eleves-parents/{id}/`), pour le défaire. */
  id_lien: number;
  id_eleve: number;
  nom_complet: string;
  matricule: string;
  lien: 'pere' | 'mere' | 'tuteur' | 'autre';
  lien_libelle: string;
  contact_principal: boolean;
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
  /** Présence dans l'école — à ne pas confondre avec l'état de l'inscription. */
  statut: 'actif' | 'radie' | 'transfere' | 'diplome';
  /** Où en est sa scolarité sur l'année consultée. */
  inscription_statut?: 'en_attente' | 'en_cours' | 'terminee' | 'annulee' | null;
  inscription_libelle?: string | null;
  /** Inscrit incomplet que l'administration a autorisé à suivre les cours. */
  inscription_autorise_en_classe?: boolean;
  classe?: string | null;
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
  /** Parent déjà enregistré (frère ou sœur inscrit) : rattaché au lieu d'être recréé. */
  id_parent?: number;
  parent_nom_complet?: string;
  parent_telephone?: string;
  parent_email?: string;
  parent_lien?: 'pere' | 'mere' | 'tuteur' | 'autre';
  parent_adresse?: string;
  parent_profession?: string;
  numero_registre?: string;
  /** Fiche sanitaire remplie à l'inscription (rubriques renseignées seulement). */
  sante?: Partial<FicheSante>;
  /** Âge hors fourchette : la personne a vu l'avertissement et confirme. */
  derogation_age?: boolean;
  /** Types de frais cochés en plus de l'inscription et des mensualités. */
  frais_optionnels?: number[];
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
  /** Nom choisi par l'établissement. */
  libelle: string;
  /** Ce qui décide des règles : inscription et mensualité sont facturées d'office. */
  nature: 'inscription' | 'mensualite' | 'autre';
  nature_libelle?: string;
  montant_defaut: number;
  frequence?: string;
}

export type MethodePaiement =
  | 'orange_money' | 'wave' | 'free_money' | 'carte_bancaire' | 'especes';

/**
 * Statut d'ensemble d'un élève ou d'un mois, calculé côté serveur.
 *
 * `sans_frais` n'est pas « à jour » : rien n'a été facturé, donc rien n'a été
 * payé. Les deux donnent un reste à zéro, pour des raisons opposées.
 */
export type StatutFinancier =
  | 'a_jour' | 'partiel' | 'en_retard' | 'non_paye' | 'sans_frais';

export interface FraisEleve {
  id: number;
  id_eleve: number;
  eleve_nom?: string;
  eleve_prenom?: string;
  id_type_frais: number;
  type_frais?: string;
  id_annee_scolaire: number;
  /** Tarif avant remise : jamais masqué, même quand une remise s'applique. */
  montant_du: number;
  remise?: number;
  motif_remise?: string | null;
  /** montant_du − remise : ce que la famille doit réellement. */
  montant_net?: number;
  montant_paye?: number;
  solde_restant?: number;
  echeance?: string;
  en_retard?: boolean;
  statut: 'paye' | 'partiel' | 'impaye';
}

export interface Paiement {
  id: number;
  id_frais_eleve: number;
  eleve_nom?: string;
  montant: number;
  date_paiement: string;
  methode: MethodePaiement;
  reference_transaction?: string;
  commentaire?: string | null;
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
  canal: 'whatsapp' | 'email';
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
  /** `null` quand le montant varie selon le niveau de chaque élève. */
  montant: number | null;
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
  /** Combien d'élèves dans chaque situation — le montant global ne le dit pas. */
  eleves?: Record<StatutFinancier | 'total', number>;
  derniers_paiements?: {
    id: number;
    date: string;
    montant: number;
    eleve: string;
    id_eleve: number;
    type_frais: string;
    methode: string;
    reference_transaction: string | null;
  }[];
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
/** Fiche sanitaire : GET / PUT /eleves/{id}/sante/ (module « sante »). */
export interface FicheSante {
  groupe_sanguin: string | null;
  allergies: string | null;
  maladies_chroniques: string | null;
  traitements: string | null;
  besoins_particuliers: string | null;
  vaccinations_a_jour: boolean | null;
  medecin_nom: string | null;
  medecin_telephone: string | null;
  contact_urgence_nom: string | null;
  contact_urgence_telephone: string | null;
  remarques: string | null;
  date_maj?: string | null;
  maj_par?: string | null;
}

export interface DossierEleve {
  eleve: {
    id: number;
    matricule: string;
    /** Numéro du registre matricule tenu par l'école, distinct du matricule. */
    numero_registre: string | null;
    nom: string;
    prenom: string;
    sexe: string | null;
    date_naissance: string | null;
    adresse: string | null;
    photo_url: string | null;
    /** Une photo est enregistrée : elle se lit par GET /eleves/{id}/photo/. */
    a_photo: boolean;
    /** L'élève a un compte de connexion rattaché à sa fiche. */
    a_un_acces: boolean;
    acces_email: string | null;
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
    statut_code: 'en_attente' | 'en_cours' | 'terminee' | 'annulee';
    /** Exception : inscrit incomplet autorisé à suivre les cours. */
    autorise_en_classe: boolean;
    motif_autorisation: string | null;
    date_autorisation: string | null;
    autorise_par: string | null;
    /** Droits d'inscription de l'année réglés : condition de l'autorisation. */
    droits_regles: boolean;
  } | null;
  parents: {
    id: number;
    nom_complet: string;
    lien: string;
    telephone: string | null;
    email: string | null;
    adresse: string | null;
    contact_principal: boolean;
  }[] | null;
  /** Statut de cas social ; null pour qui ne lit pas les frais. */
  cas_social: { actif: boolean; taux: string | number; motif: string | null } | null;
  /** Fiche sanitaire ; null pour qui n'a pas accès au module « sante ». */
  sante: FicheSante | null;
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
  }[] | null;
  /**
   * Les sections parents, finances, résultats et assiduité valent `null` quand
   * le profil n'a pas le droit de lire le module dont elles relèvent.
   *
   * Résumé de l'année en cours, et d'elle seule : le détail complet vit dans
   * `SituationFinanciereEleve`, servi par l'onglet Finances.
   */
  finances: {
    id_annee_scolaire: number | null;
    annee: string | null;
    frais_initial: number;
    remise: number;
    total_du: number;
    total_paye: number;
    solde: number;
    statut: StatutFinancier;
    statut_libelle: string;
    frais_impayes: number;
    frais_en_retard: number;
    prochaine_echeance: string | null;
  } | null;
  assiduite: { absences: number; retards: number; non_justifiees: number } | null;
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
  /** Lignes de frais créées (0 dans l'aperçu). */
  frais_emis: number;
  ignores: number;
  /** Élèves de la classe de départ décochés : ils restent à réinscrire. */
  non_retenus: number;
  eleves: {
    id: number;
    matricule: string;
    nom_complet: string;
    deja_inscrit: boolean;
    retenu: boolean;
    /** Âge hors de la fourchette du niveau visé, ou date de naissance manquante. */
    alerte_age: { code: string; message: string; bloquant: boolean } | null;
  }[];
  /** Élèves retenus dont l'âge est à vérifier. */
  alertes_age: number;
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

/** Réponse de GET /frais-eleves/impayes/par-classe/. */
export interface ImpayesParClasse {
  annee: string | null;
  detail?: string;
  id_annee_scolaire?: number;
  total_reste: number;
  classes: {
    id_classe: number;
    classe: string;
    eleves: number;
    /** Élèves dont au moins une échéance est dépassée. */
    eleves_en_retard: number;
    du: number;
    paye: number;
    reste: number;
    taux: number;
  }[];
}

/** Réponse de GET /absences/synthese/ — assiduité sur une fenêtre glissante. */
export interface SyntheseAssiduite {
  annee: string | null;
  detail?: string;
  jours?: number;
  depuis?: string;
  total?: { absences: number; retards: number; non_justifiees: number; eleves: number };
  classes: {
    id_classe: number;
    classe: string;
    absences: number;
    retards: number;
    non_justifiees: number;
    eleves: number;
  }[];
  eleves: {
    id_eleve: number;
    nom: string;
    matricule: string;
    classe: string;
    absences: number;
    retards: number;
    non_justifiees: number;
  }[];
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

// ---------------------------------------------------------------------------
// Situation financière — dossier de l'élève et vue d'ensemble
// ---------------------------------------------------------------------------

/** Une ligne de frais telle que l'affiche l'onglet Finances du dossier. */
export interface LigneFrais {
  id: number;
  libelle: string;
  type_frais: string;
  id_type_frais: number;
  mois: string | null;
  echeance: string | null;
  montant_initial: number;
  remise: number;
  motif_remise: string | null;
  montant: number;
  paye: number;
  reste: number;
  statut: 'paye' | 'partiel' | 'impaye';
  statut_libelle: string;
  en_retard: boolean;
}

/** Un mois de l'échéancier. `sans_frais` = rien n'était dû ce mois-là. */
export interface LigneEcheancier {
  mois: string | null;
  libelle: string;
  du: number;
  paye: number;
  reste: number;
  statut: StatutFinancier;
  statut_libelle: string;
}

export interface LignePaiement {
  id: number;
  date: string;
  montant: number;
  id_frais_eleve: number;
  frais: string;
  type_frais: string;
  mois: string | null;
  methode: MethodePaiement;
  methode_libelle: string;
  reference_transaction: string | null;
  commentaire: string | null;
  id_recu: number | null;
  numero_recu: string | null;
  enregistre_par: string | null;
  statut: string;
}

/** Réponse de GET /eleves/{id}/finances/?id_annee_scolaire= */
export interface SituationFinanciereEleve {
  eleve: {
    id: number;
    matricule: string;
    nom: string;
    prenom: string;
    nom_complet: string;
    statut: string;
  };
  annee: {
    id: number;
    libelle: string;
    statut: string;
    date_debut: string;
    date_fin: string;
  } | null;
  /** Toutes les années où l'élève a laissé une trace : le sélecteur du dossier. */
  annees: {
    id: number;
    libelle: string;
    statut: string;
    classe: string | null;
    niveau: string | null;
  }[];
  scolarite: {
    id_inscription: number;
    id_classe: number;
    classe: string;
    niveau: string;
    date_inscription: string;
    statut: string;
  } | null;
  resume: {
    frais_initial: number;
    remise: number;
    total_du: number;
    total_paye: number;
    reste: number;
    statut: StatutFinancier;
    statut_libelle: string;
    taux: number;
    nombre_frais: number;
    frais_soldes: number;
    frais_en_retard: number;
    prochaine_echeance: string | null;
    /** Frais dus d'office jamais émis : tant qu'il en manque, l'élève n'est pas à jour. */
    frais_manquants?: string[];
  };
  /**
   * Où en est la confirmation de l'inscription : droits d'inscription ET
   * premier mois, intégralement réglés, font passer de « Inscrit incomplet »
   * à « Inscrit ».
   */
  confirmation: {
    statut: string | null;
    statut_libelle: string | null;
    confirmee: boolean;
    /** Faux = aucun frais d'inscription émis : rien ne peut confirmer. */
    frais_emis: boolean;
    droits_dus: number;
    droits_payes: number;
    droits_restants: number;
    /** Faux = aucune mensualité émise : rien ne peut confirmer. */
    premier_mois_emis: boolean;
    /** « Octobre 2026 ». */
    premier_mois: string | null;
    premier_mois_du: number;
    premier_mois_paye: number;
    premier_mois_restant: number;
  } | null;
  frais: LigneFrais[];
  echeancier: LigneEcheancier[];
  paiements: LignePaiement[];
}

/** Réponse de POST /paiements/encaisser/ : les écritures et le dossier remis à jour. */
export interface ResultatEncaissement {
  paiements: Paiement[];
  situation: SituationFinanciereEleve;
  /** Renseigné quand ce versement fait passer l'élève d'« inscrit incomplet » à « Inscrit ». */
  inscription_confirmee?: { eleve: string; classe: string | null; annee: string } | null;
}

/** Réponse de GET /eleves/{id}/frais-disponibles/ — ce qu'« Ajouter des frais » propose. */
export interface FraisDisponibles {
  annee: { id: number; libelle: string; date_debut: string; date_fin: string };
  classe: string;
  niveau: string | null;
  types: {
    id: number;
    libelle: string;
    montant: number;
    tarif_niveau: boolean;
    mensuel: boolean;
    /** L'élève a déjà ce frais sur l'année. */
    deja: boolean;
  }[];
}

/** Réponse de POST /eleves/{id}/ajouter-frais/. */
export interface ResultatAjoutFrais {
  id_annee_scolaire: number;
  annee: string;
  crees: number;
  total: number;
  frais: { libelle: string; echeances: number; montant: number }[];
  deja: string[];
  sans_tarif: string[];
  situation: SituationFinanciereEleve;
}

/** Réponse de GET /caisse/historique/ — une journée de caisse. */
export interface HistoriqueCaisse {
  date: string;
  caissier: { id: number; nom: string } | null;
  caissiers: { id: number; nom: string; role: string }[];
  totaux: {
    encaisse: number;
    depense: number;
    solde: number;
    especes: number;
    especes_en_caisse: number;
    nombre_paiements: number;
    nombre_depenses: number;
    par_methode: { methode: string; libelle: string; montant: number; nombre: number }[];
  };
  par_caissier: {
    id: number | null;
    nom: string;
    encaisse: number;
    depense: number;
    nombre_paiements: number;
    nombre_depenses: number;
  }[];
  paiements: {
    id: number;
    heure: string;
    id_eleve: number;
    eleve: string;
    matricule: string;
    frais: string;
    montant: number;
    methode: string;
    methode_libelle: string;
    reference: string | null;
    numero_recu: string | null;
    id_recu: number | null;
    id_caissier: number | null;
    caissier: string;
  }[];
  depenses: {
    id: number;
    categorie: string;
    motif: string;
    montant: number;
    id_caissier: number | null;
    caissier: string;
    /** Une facture ou un reçu est joint à la dépense. */
    a_justificatif: boolean;
  }[];
  /** L'arrêté du jour, s'il a été fait : totaux figés au moment du comptage. */
  cloture: {
    id: number;
    date_cloture: string;
    par: string;
    total_encaisse: number;
    total_depense: number;
    especes_attendues: number;
    commentaire: string | null;
    /** Opérations enregistrées après la clôture ; `null` si la vue est filtrée sur un caissier. */
    ecart_encaisse: number | null;
    ecart_depense: number | null;
  } | null;
}

/** Réponse de GET /frais-eleves/situation/ — un élève par ligne. */
export interface SituationGlobale {
  annee: string | null;
  id_annee_scolaire?: number;
  eleves: {
    id_eleve: number;
    matricule: string;
    nom_complet: string;
    id_classe: number;
    classe: string;
    id_niveau: number;
    niveau: string;
    frais_initial: number;
    remise: number;
    du: number;
    paye: number;
    reste: number;
    statut: StatutFinancier;
    statut_libelle: string;
  }[];
  totaux: { eleves: number; du: number; paye: number; reste: number };
  repartition: Partial<Record<StatutFinancier, number>>;
  detail?: string;
}

// ---------------------------------------------------------------------------
// Rentrée : file d'attente et réinscription individuelle
// ---------------------------------------------------------------------------

/** Une ligne de GET /eleves/a-reinscrire/ : l'élève, et d'où il vient. */
export interface EleveAReinscrire {
  id: number;
  matricule: string;
  nom: string;
  prenom: string;
  nom_complet: string;
  sexe: string | null;
  derniere_annee: string;
  id_derniere_classe: number;
  derniere_classe: string;
  id_dernier_niveau: number;
  dernier_niveau: string;
}

export interface FileReinscription {
  annee: string | null;
  id_annee_scolaire?: number;
  total: number;
  page: number;
  pages: number;
  taille_page: number;
  precedent: boolean;
  suivant: boolean;
  eleves: EleveAReinscrire[];
  detail?: string;
}

/** Réponse de GET /eleves/{id}/reinscription/ : de quoi pré-remplir l'écran. */
export interface ContexteReinscription {
  eleve: {
    id: number;
    matricule: string;
    nom: string;
    prenom: string;
    nom_complet: string;
    sexe: string | null;
    statut: string;
    statut_libelle: string;
  };
  precedente: {
    annee: string;
    id_annee_scolaire: number;
    classe: string;
    id_classe: number;
    niveau: string | null;
    id_niveau: number | null;
  } | null;
  annee_cible: { id: number; libelle: string; date_debut: string };
  /** Renseigné si l'élève est déjà passé : l'écran le dit au lieu d'échouer. */
  deja_inscrit: { classe: string; statut: string; statut_libelle: string } | null;
  /** Catalogue des frais : ceux d'office imposés, ceux de l'an passé pré-cochés. */
  types_frais: {
    id: number;
    libelle: string;
    libelle_affiche: string;
    nature: 'inscription' | 'mensualite' | 'autre';
    montant_defaut: number;
    mensuel: boolean;
    d_office: boolean;
    reconduit: boolean;
    /** Montant par identifiant de niveau ; absent, le montant par défaut s'applique. */
    tarifs: Record<string, number>;
  }[];
  classes: {
    id: number;
    nom: string;
    niveau: string | null;
    id_niveau: number | null;
    effectif: number;
    capacite_max: number;
  }[];
}

/** Réponse de POST /eleves/{id}/reinscrire/. */
export interface ResultatReinscriptionEleve {
  id_inscription: number;
  id_eleve: number;
  eleve: string;
  annee_precedente: string;
  classe_precedente: string;
  annee: string;
  id_annee_scolaire: number;
  classe: string;
  id_classe: number;
  date_inscription: string;
  /** `en_attente` tant qu'aucun frais n'a été réglé. */
  statut: 'en_attente' | 'en_cours' | 'terminee' | 'annulee';
  statut_libelle: string;
  /** Lignes de frais créées pour la nouvelle année. */
  frais_emis: number;
  effectif_classe: number;
  avertissement: string | null;
}

/** Facture d'un élève pour une année : renvoyée à l'inscription et ouverte au guichet. */
export interface FactureEleve {
  id_eleve: number;
  matricule: string;
  nom: string;
  prenom: string;
  classe: string | null;
  id_annee_scolaire: number;
  annee: string;
  statut_inscription: string | null;
  statut_inscription_libelle: string | null;
  lignes: {
    id: number;
    libelle: string;
    echeance: string | null;
    montant: number;
    paye: number;
    reste: number;
  }[];
  total_du: number;
  total_paye: number;
  reste: number;
}

// ---------------------------------------------------------------------------
// Plateforme — réservée au super administrateur
// ---------------------------------------------------------------------------

export type PlanAbonnement = 'essai' | 'standard' | 'pro';
export type StatutEcole = 'actif' | 'suspendu' | 'resilie';

/** Un compte de direction d'une école, vu depuis la plateforme. */
export interface AdminEcole {
  id: number;
  nom: string;
  prenom: string;
  email: string;
  telephone: string | null;
  role: Role;
  role_libelle: string;
  statut: 'actif' | 'inactif';
  date_creation: string;
}

/** Réponse de /plateforme/ecoles/. */
export interface EcolePlateforme {
  id: number;
  nom: string;
  code_ecole: string;
  adresse: string | null;
  telephone: string | null;
  email: string | null;
  plan_abonnement: PlanAbonnement;
  plan_libelle: string;
  abonnement_paye: boolean;
  date_paiement: string | null;
  statut: StatutEcole;
  statut_libelle: string;
  date_activation: string | null;
  date_creation: string;
  nb_utilisateurs: number;
  nb_eleves: number;
  admins: AdminEcole[];
}
