/**
 * Un module (Parents, Matières, Présences...) se définit entièrement par
 * cette configuration : le composant générique lit ça et affiche la liste
 * + le formulaire correspondant, sans code dédié à écrire.
 */
export interface ColumnConfig {
  key: string;
  label: string;
  /** Pour formater une valeur liée (ex: afficher id_niveau -> "6ème") */
  displayKey?: string;
  type?: 'text' | 'date' | 'badge' | 'number';
}

export interface FieldOption {
  value: string | number;
  label: string;
}

/**
 * Valeur affichée en lecture seule, lue sur l'option choisie dans un autre
 * champ du formulaire (ex: le budget restant de la catégorie sélectionnée).
 */
export interface SourceLecture {
  /** Champ `select` dont on suit le choix. */
  champ: string;
  /** Clé à lire sur l'option choisie. */
  cle: string;
  /** Unité ajoutée après la valeur, ex: « F CFA ». */
  suffixe?: string;
  /**
   * Champ de saisie retranché à la valeur au fil de la frappe : l'affichage
   * montre ce qui resterait une fois la saisie enregistrée, et le formulaire
   * avertit dès que ça passe en dessous de zéro.
   */
  compare?: string;
  /**
   * Clé de l'enveloppe totale sur l'option choisie, ex: `budget_alloue`.
   * Présente, elle fait tracer une jauge sous le champ ; absente, il n'y a
   * rien à quoi rapporter la consommation et le champ reste seul.
   */
  total?: string;
}

/**
 * Pièce jointe déposée sur une sous-route du détail (ex:
 * /api/depenses/12/justificatif/), en GET pour la relire, POST (champ
 * `fichier`) pour la déposer, DELETE pour la retirer.
 */
export interface FichierConfig {
  /** Sous-route, sans slash initial ni final. */
  chemin: string;
  /** Booléen de la ligne qui dit si une pièce est déjà jointe. */
  presente: string;
  /** Types proposés par le sélecteur de fichiers, ex: `application/pdf,image/*`. */
  accept: string;
}

export interface FieldConfig {
  key: string;
  label: string;
  /**
   * `lecture` n'est pas saisissable : le champ n'a pas de contrôle de
   * formulaire et n'est pas envoyé à l'API, il ne fait qu'afficher.
   * `fichier` non plus : la pièce part à part, une fois la ligne enregistrée.
   */
  type: 'text' | 'email' | 'tel' | 'password' | 'number' | 'date' | 'time' | 'select' | 'textarea' | 'checkbox' | 'lecture' | 'fichier';
  /** Texte d'aide affiché sous le champ. */
  aide?: string;
  /** Requis par les champs `lecture` : d'où vient la valeur affichée. */
  source?: SourceLecture;
  /** Requis par les champs `fichier` : où déposer la pièce. */
  fichier?: FichierConfig;
  required?: boolean;
  /** Options statiques (ex: enum Django TextChoices) */
  options?: FieldOption[];
  /** Options chargées dynamiquement depuis un autre endpoint (ex: liste des classes) */
  optionsEndpoint?: string;
  optionsLabelKey?: string;
  /**
   * Seconde clé, affichée entre parenthèses après le libellé (un matricule,
   * par exemple) : deux élèves peuvent porter le même nom complet.
   */
  optionsSecondaireKey?: string;
  /**
   * Liste dépendante d'un autre champ : le choix fait dans `champ` est envoyé
   * à l'API comme paramètre `param` pour restreindre les options.
   *
   * Tant que le champ source est vide, la liste reste vide : proposer les
   * 500 élèves de l'école quand on cherche ceux d'une classe n'aide personne.
   */
  optionsFiltrePar?: { champ: string; param: string };
  placeholder?: string;
  span2?: boolean;
  /**
   * Valeur proposée à la création, que l'utilisateur peut changer.
   * `'aujourdhui'` pour un champ `date` : la date du jour.
   */
  defaut?: 'aujourdhui';
}

/**
 * Action métier déclenchée sur une ligne (POST sur une sous-route du détail,
 * ex: /api/communications/12/envoyer/).
 */
export interface RowAction {
  label: string;
  /** Sous-route appelée en POST, sans slash initial ni final. */
  chemin: string;
  /** Message de confirmation ; absent = pas de confirmation. */
  confirmation?: string;
  /** L'action n'est proposée que si la ligne a cette valeur sur ce champ. */
  visibleSi?: { champ: string; valeur: unknown };
}

/**
 * Liste déroulante au-dessus du tableau, qui restreint la liste par un
 * paramètre d'URL (ex: les classes d'un niveau).
 */
export interface FiltreConfig {
  /** Paramètre envoyé à l'API, que l'endpoint doit réellement accepter. */
  param: string;
  /** Libellé de l'option qui retire le filtre, ex: « Tous les niveaux ». */
  tous: string;
  optionsEndpoint: string;
  optionsLabelKey?: string;
  /**
   * Pas d'option « tous » : la liste montre une valeur à la fois, la première
   * par défaut, et jamais tout d'un coup.
   */
  obligatoire?: boolean;
}

/** Page à ouvrir ensuite : rappelée sous le titre et proposée après un enregistrement. */
export interface EtapeSuivante {
  label: string;
  chemin: string;
  /**
   * Module de la page visée, `'module'` ou `'module:ecriture'` : l'étape
   * disparaît pour un profil qui n'y a pas accès. Proposer la suite d'un
   * parcours fermé serait une impasse.
   */
  module?: string;
}

/** Raccourci sur chaque ligne, ex: la liste des élèves d'une classe. */
export interface LienLigne {
  label: string;
  chemin: string;
  /** Module de la page visée : le raccourci disparaît sans accès (voir `EtapeSuivante`). */
  module?: string;
  /** Paramètre d'URL qui reçoit la valeur de la ligne. */
  parametre: string;
  /** Champ de la ligne à transmettre ; `id` par défaut. */
  champ?: string;
}

/**
 * La fiche peut recevoir un compte de connexion (bouton « Créer son accès »).
 *
 * `role` n'est là que pour l'annoncer à l'écran : c'est le serveur qui décide
 * du rôle donné au compte.
 */
export interface AccesConfig {
  role: string;
  /** Champ de la ligne qui la nomme dans la fenêtre ; `nom_complet` par défaut. */
  libelle?: string;
}

export interface EntityConfig {
  key: string;
  endpoint: string;
  titre: string;
  titreSingulier: string;
  eyebrow: string;
  description: string;
  columns: ColumnConfig[];
  fields: FieldConfig[];
  /** Consultation seule : ni ajout, ni modification, ni suppression (ex: journal d'audit). */
  lectureSeule?: boolean;
  /** Fiches dont on peut créer le compte de connexion (parents, enseignants). */
  acces?: AccesConfig;
  /** Actions métier proposées sur chaque ligne. */
  actions?: RowAction[];
  /** Listes déroulantes de filtrage au-dessus du tableau. */
  filtres?: FiltreConfig[];
  /** Pages qui suivent logiquement celle-ci. */
  suite?: EtapeSuivante[];
  /** Raccourcis proposés sur chaque ligne. */
  liens?: LienLigne[];
  /**
   * La liste suit l'année scolaire choisie dans le bandeau haut.
   *
   * À n'activer que sur les listes dont l'endpoint accepte réellement
   * `id_annee_scolaire` : le poser ailleurs le ferait ignorer en silence, et
   * l'écran prétendrait filtrer sans le faire.
   */
  filtreParAnnee?: boolean;
  /**
   * Année à préremplir dans le formulaire de création, quand l'entité porte
   * une clé vers l'année scolaire. Évite de la ressaisir à chaque ligne.
   */
  champAnnee?: string;
}
