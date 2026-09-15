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

export interface FieldConfig {
  key: string;
  label: string;
  type: 'text' | 'email' | 'tel' | 'password' | 'number' | 'date' | 'time' | 'select' | 'textarea' | 'checkbox';
  /** Texte d'aide affiché sous le champ. */
  aide?: string;
  required?: boolean;
  /** Options statiques (ex: enum Django TextChoices) */
  options?: FieldOption[];
  /** Options chargées dynamiquement depuis un autre endpoint (ex: liste des classes) */
  optionsEndpoint?: string;
  optionsLabelKey?: string;
  placeholder?: string;
  span2?: boolean;
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
  /** Actions métier proposées sur chaque ligne. */
  actions?: RowAction[];
  /** Listes déroulantes de filtrage au-dessus du tableau. */
  filtres?: FiltreConfig[];
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
