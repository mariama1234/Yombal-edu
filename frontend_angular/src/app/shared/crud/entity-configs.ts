import { EntityConfig } from './crud-config.model';

/**
 * Une configuration par module. Pour ajouter un nouveau module au menu,
 * il suffit d'ajouter une entrée ici — aucun composant à créer.
 */
export const ENTITY_CONFIGS: Record<string, EntityConfig> = {

  parents: {
    key: 'parents',
    endpoint: 'parents',
    titre: 'Parents & tuteurs',
    titreSingulier: 'un contact',
    eyebrow: 'Général',
    description: 'Contacts responsables des élèves.',
    columns: [
      { key: 'nom', label: 'Nom' },
      { key: 'prenom', label: 'Prénom' },
      { key: 'telephone', label: 'Téléphone' },
      { key: 'email', label: 'E-mail' },
    ],
    fields: [
      { key: 'nom', label: 'Nom', type: 'text', required: true },
      { key: 'prenom', label: 'Prénom', type: 'text', required: true },
      { key: 'profession', label: 'Profession', type: 'text' },
      { key: 'telephone', label: 'Téléphone', type: 'tel', required: true },
      { key: 'email', label: 'E-mail', type: 'email' },
    ],
  },

  matieres: {
    key: 'matieres',
    endpoint: 'matieres',
    titre: 'Matières',
    titreSingulier: 'une matière',
    eyebrow: 'Pédagogie',
    description: 'Catalogue des matières enseignées dans l\u2019école.',
    columns: [{ key: 'nom', label: 'Nom de la matière' }],
    fields: [{ key: 'nom', label: 'Nom de la matière', type: 'text', required: true, placeholder: 'Mathématiques' }],
  },

  personnel: {
    key: 'personnel',
    endpoint: 'enseignants',
    titre: 'Enseignants & personnel',
    titreSingulier: 'un enseignant',
    eyebrow: 'Gestion',
    description: 'Équipe pédagogique de l\u2019école.',
    columns: [
      { key: 'matricule', label: 'Matricule' },
      { key: 'specialite', label: 'Spécialité' },
      { key: 'statut', label: 'Statut', type: 'badge' },
    ],
    fields: [
      { key: 'matricule', label: 'Matricule', type: 'text', required: true },
      { key: 'specialite', label: 'Spécialité', type: 'text' },
      { key: 'date_embauche', label: "Date d'embauche", type: 'date' },
      {
        key: 'statut', label: 'Statut', type: 'select', required: true,
        options: [{ value: 'actif', label: 'Actif' }, { value: 'inactif', label: 'Inactif' }],
      },
    ],
  },

  presences: {
    key: 'presences',
    endpoint: 'absences',
    titre: 'Présences',
    titreSingulier: 'une absence',
    eyebrow: 'Pédagogie',
    description: 'Absences et retards enregistrés par classe.',
    columns: [
      { key: 'date_absence', label: 'Date', type: 'date' },
      { key: 'statut', label: 'Type', type: 'badge' },
      { key: 'justifiee', label: 'Justifiée' },
      { key: 'motif', label: 'Motif' },
    ],
    fields: [
      { key: 'id_eleve', label: 'Élève', type: 'select', required: true, optionsEndpoint: 'eleves', optionsLabelKey: 'nom' },
      { key: 'id_classe', label: 'Classe', type: 'select', required: true, optionsEndpoint: 'classes', optionsLabelKey: 'nom' },
      {
        key: 'statut', label: 'Type', type: 'select', required: true,
        options: [{ value: 'absent', label: 'Absent' }, { value: 'retard', label: 'Retard' }],
      },
      { key: 'date_absence', label: 'Date et heure', type: 'date', required: true },
      { key: 'justifiee', label: 'Absence justifiée', type: 'checkbox' },
      { key: 'motif', label: 'Motif', type: 'textarea', span2: true },
    ],
  },

  notes: {
    key: 'notes',
    endpoint: 'notes',
    titre: 'Notes & bulletins',
    titreSingulier: 'une note',
    eyebrow: 'Pédagogie',
    description: 'Saisie des notes par évaluation. Les bulletins sont calculés automatiquement à partir de ces notes.',
    columns: [
      { key: 'valeur', label: 'Note', type: 'number' },
      { key: 'appreciation', label: 'Appréciation' },
      { key: 'date_saisie', label: 'Saisie le', type: 'date' },
    ],
    fields: [
      { key: 'id_eleve', label: 'Élève', type: 'select', required: true, optionsEndpoint: 'eleves', optionsLabelKey: 'nom' },
      { key: 'id_evaluation', label: 'Évaluation', type: 'select', required: true, optionsEndpoint: 'evaluations', optionsLabelKey: 'type' },
      { key: 'valeur', label: 'Note (sur 20)', type: 'number', required: true },
      { key: 'appreciation', label: 'Appréciation', type: 'textarea', span2: true },
    ],
  },

  'emploi-du-temps': {
    key: 'emploi-du-temps',
    endpoint: 'emplois-du-temps',
    titre: 'Emploi du temps',
    titreSingulier: 'un cours',
    eyebrow: 'Pédagogie',
    description: 'Planning hebdomadaire des cours par classe.',
    columns: [
      { key: 'jour_semaine', label: 'Jour' },
      { key: 'heure_debut', label: 'Début' },
      { key: 'heure_fin', label: 'Fin' },
      { key: 'matiere_nom', label: 'Matière' },
      { key: 'salle_nom', label: 'Salle' },
    ],
    fields: [
      { key: 'id_classe', label: 'Classe', type: 'select', required: true, optionsEndpoint: 'classes', optionsLabelKey: 'nom' },
      { key: 'id_matiere', label: 'Matière', type: 'select', required: true, optionsEndpoint: 'matieres', optionsLabelKey: 'nom' },
      { key: 'id_enseignant', label: 'Enseignant', type: 'select', required: true, optionsEndpoint: 'enseignants', optionsLabelKey: 'matricule' },
      { key: 'id_salle', label: 'Salle', type: 'select', optionsEndpoint: 'salles', optionsLabelKey: 'nom' },
      {
        key: 'jour_semaine', label: 'Jour', type: 'select', required: true,
        options: ['lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi'].map(j => ({ value: j, label: j[0].toUpperCase() + j.slice(1) })),
      },
      { key: 'heure_debut', label: 'Heure de début', type: 'time', required: true },
      { key: 'heure_fin', label: 'Heure de fin', type: 'time', required: true },
    ],
  },

  classes: {
    key: 'classes',
    endpoint: 'classes',
    titre: 'Classes & niveaux',
    titreSingulier: 'une classe',
    eyebrow: 'Pédagogie',
    description: 'Classes actives de l\u2019établissement.',
    columns: [
      { key: 'nom', label: 'Classe' },
      { key: 'niveau_libelle', label: 'Niveau' },
      { key: 'effectif', label: 'Effectif', type: 'number' },
      { key: 'capacite_max', label: 'Capacité', type: 'number' },
    ],
    fields: [
      { key: 'nom', label: 'Nom de la classe', type: 'text', required: true, placeholder: '6ème A' },
      { key: 'id_niveau', label: 'Niveau', type: 'select', required: true, optionsEndpoint: 'niveaux', optionsLabelKey: 'libelle' },
      { key: 'id_annee_scolaire', label: 'Année scolaire', type: 'select', required: true, optionsEndpoint: 'annees-scolaires', optionsLabelKey: 'libelle' },
      { key: 'capacite_max', label: 'Capacité maximale', type: 'number', required: true },
      { key: 'id_enseignant_principal', label: 'Professeur principal', type: 'select', optionsEndpoint: 'enseignants', optionsLabelKey: 'matricule' },
    ],
  },

  'types-frais': {
    key: 'types-frais',
    endpoint: 'types-frais',
    titre: 'Types de frais',
    titreSingulier: 'un type de frais',
    eyebrow: 'Gestion',
    description: 'Catalogue des frais applicables (mensualité, transport, uniforme...).',
    columns: [
      { key: 'libelle', label: 'Libellé' },
      { key: 'montant_defaut', label: 'Montant par défaut', type: 'number' },
      { key: 'frequence', label: 'Fréquence' },
    ],
    fields: [
      {
        key: 'libelle', label: 'Libellé', type: 'select', required: true,
        options: ['inscription', 'mensualite', 'demi_pension', 'transport', 'uniforme', 'fournitures', 'autre']
          .map(v => ({ value: v, label: v.replace('_', ' ') })),
      },
      { key: 'montant_defaut', label: 'Montant par défaut (F CFA)', type: 'number', required: true },
      { key: 'frequence', label: 'Fréquence', type: 'text', placeholder: 'Mensuelle' },
    ],
  },

  depenses: {
    key: 'depenses',
    endpoint: 'depenses',
    titre: 'Dépenses',
    titreSingulier: 'une dépense',
    eyebrow: 'Gestion',
    description: 'Dépenses de l\u2019école, catégorisées.',
    columns: [
      { key: 'motif', label: 'Motif' },
      { key: 'montant', label: 'Montant', type: 'number' },
      { key: 'date_depense', label: 'Date', type: 'date' },
    ],
    fields: [
      { key: 'motif', label: 'Motif', type: 'text', required: true, span2: true },
      { key: 'id_type_depense', label: 'Catégorie', type: 'select', required: true, optionsEndpoint: 'types-depenses', optionsLabelKey: 'libelle' },
      { key: 'montant', label: 'Montant (F CFA)', type: 'number', required: true },
      { key: 'date_depense', label: 'Date', type: 'date', required: true },
    ],
  },

  communications: {
    key: 'communications',
    endpoint: 'communications',
    titre: 'Communications',
    titreSingulier: 'une communication',
    eyebrow: 'Gestion',
    description: 'Annonces et messages aux familles. Une communication se prépare en brouillon, puis s’envoie : les destinataires sont alors calculés à partir de la cible et du canal choisis.',
    columns: [
      { key: 'objet', label: 'Objet' },
      { key: 'canal', label: 'Canal', type: 'badge' },
      { key: 'cible', label: 'Cible' },
      { key: 'statut', label: 'Statut', type: 'badge' },
      { key: 'nb_destinataires', label: 'Destinataires', type: 'number' },
      { key: 'nb_lus', label: 'Lus', type: 'number' },
    ],
    actions: [
      {
        label: 'Envoyer',
        chemin: 'envoyer',
        confirmation: 'Envoyer cette communication à tous les destinataires correspondant à la cible ?',
        visibleSi: { champ: 'statut', valeur: 'brouillon' },
      },
    ],
    fields: [
      { key: 'objet', label: 'Objet', type: 'text', required: true, span2: true },
      { key: 'contenu', label: 'Message', type: 'textarea', required: true, span2: true },
      {
        key: 'type_communication', label: 'Type', type: 'select', required: true,
        options: [
          { value: 'annonce', label: 'Annonce' }, { value: 'message', label: 'Message' },
          { value: 'convocation', label: 'Convocation' }, { value: 'alerte', label: 'Alerte' },
        ],
      },
      {
        key: 'canal', label: 'Canal', type: 'select', required: true,
        options: [
          { value: 'interne', label: 'Interne (comptes de l’app)' },
          { value: 'sms', label: 'SMS (parents & tuteurs)' },
          { value: 'email', label: 'E-mail (parents & tuteurs)' },
        ],
      },
      {
        key: 'cible', label: 'Destinataires', type: 'select', required: true,
        options: [
          { value: 'ecole', label: 'Toute l’école' }, { value: 'classe', label: 'Une classe' },
          { value: 'role', label: 'Un rôle' }, { value: 'individuel', label: 'Choisis un par un' },
        ],
      },
      { key: 'id_classe', label: 'Classe (si cible = une classe)', type: 'select', optionsEndpoint: 'classes', optionsLabelKey: 'nom' },
      {
        key: 'role_cible', label: 'Rôle (si cible = un rôle)', type: 'select',
        options: [
          { value: 'admin', label: 'Administrateurs' }, { value: 'enseignant', label: 'Enseignants' },
          { value: 'parent', label: 'Parents' }, { value: 'eleve', label: 'Élèves' },
          { value: 'caissier', label: 'Caissiers' },
        ],
      },
    ],
  },

  documents: {
    key: 'documents',
    endpoint: 'documents',
    titre: 'Documents & rapports',
    titreSingulier: 'un document',
    eyebrow: 'Gestion',
    description: 'Pièces de l’établissement et des dossiers élèves. La visibilité détermine qui voit le document dans son espace.',
    columns: [
      { key: 'titre', label: 'Titre' },
      { key: 'categorie', label: 'Catégorie', type: 'badge' },
      { key: 'visibilite', label: 'Visible par', type: 'badge' },
      { key: 'deposant_nom', label: 'Déposé par' },
      { key: 'date_ajout', label: 'Ajouté le', type: 'date' },
    ],
    fields: [
      { key: 'titre', label: 'Titre', type: 'text', required: true, span2: true },
      { key: 'description', label: 'Description', type: 'textarea', span2: true },
      {
        key: 'categorie', label: 'Catégorie', type: 'select', required: true,
        options: [
          { value: 'administratif', label: 'Administratif' }, { value: 'pedagogique', label: 'Pédagogique' },
          { value: 'financier', label: 'Financier' }, { value: 'rh', label: 'Ressources humaines' },
          { value: 'autre', label: 'Autre' },
        ],
      },
      {
        key: 'visibilite', label: 'Visible par', type: 'select', required: true,
        options: [
          { value: 'admin', label: 'Administration seule' }, { value: 'enseignants', label: 'Enseignants' },
          { value: 'parents', label: 'Parents' }, { value: 'tous', label: 'Tous les utilisateurs' },
        ],
      },
      { key: 'fichier_url', label: 'Lien du fichier', type: 'text', required: true, span2: true, placeholder: '/media/reglement-interieur.pdf' },
      { key: 'id_eleve', label: 'Rattacher au dossier d’un élève', type: 'select', optionsEndpoint: 'eleves', optionsLabelKey: 'nom' },
    ],
  },

  parametres: {
    key: 'parametres',
    endpoint: 'parametres',
    titre: 'Paramètres',
    titreSingulier: 'un paramètre',
    eyebrow: 'Administration',
    description: 'Réglages propres à l’établissement, en clé/valeur (devise, barème de notation, seuil de relance, en-tête des reçus...).',
    columns: [
      { key: 'cle', label: 'Clé' },
      { key: 'libelle', label: 'Libellé' },
      { key: 'valeur', label: 'Valeur' },
      { key: 'categorie', label: 'Catégorie', type: 'badge' },
    ],
    fields: [
      { key: 'cle', label: 'Clé', type: 'text', required: true, placeholder: 'devise' },
      { key: 'libelle', label: 'Libellé', type: 'text', required: true, placeholder: 'Devise utilisée' },
      { key: 'valeur', label: 'Valeur', type: 'text', span2: true },
      {
        key: 'type_valeur', label: 'Type de valeur', type: 'select', required: true,
        options: [
          { value: 'texte', label: 'Texte' }, { value: 'nombre', label: 'Nombre' },
          { value: 'booleen', label: 'Booléen' }, { value: 'date', label: 'Date' },
          { value: 'json', label: 'JSON' },
        ],
      },
      {
        key: 'categorie', label: 'Catégorie', type: 'select', required: true,
        options: [
          { value: 'general', label: 'Général' }, { value: 'scolarite', label: 'Scolarité' },
          { value: 'notation', label: 'Notation' }, { value: 'finance', label: 'Finance' },
          { value: 'notification', label: 'Notifications' },
        ],
      },
      { key: 'description', label: 'Description', type: 'textarea', span2: true },
    ],
  },

  utilisateurs: {
    key: 'utilisateurs',
    endpoint: 'utilisateurs',
    titre: 'Comptes & rôles',
    titreSingulier: 'un compte',
    eyebrow: 'Administration',
    description: 'Comptes de connexion de l’école. Le rôle détermine ce que chacun peut voir et faire.',
    columns: [
      { key: 'nom', label: 'Nom' },
      { key: 'prenom', label: 'Prénom' },
      { key: 'email', label: 'E-mail' },
      { key: 'role', label: 'Rôle', type: 'badge' },
      { key: 'statut', label: 'Statut', type: 'badge' },
    ],
    fields: [
      { key: 'prenom', label: 'Prénom', type: 'text', required: true },
      { key: 'nom', label: 'Nom', type: 'text', required: true },
      { key: 'email', label: 'E-mail', type: 'email', required: true, span2: true },
      {
        key: 'role', label: 'Rôle', type: 'select', required: true,
        options: [
          { value: 'admin', label: 'Administrateur' }, { value: 'enseignant', label: 'Enseignant' },
          { value: 'caissier', label: 'Caissier' }, { value: 'parent', label: 'Parent' },
          { value: 'eleve', label: 'Élève' },
        ],
      },
      { key: 'telephone', label: 'Téléphone', type: 'tel' },
      {
        key: 'password', label: 'Mot de passe', type: 'password', span2: true,
        aide: '8 caractères minimum. Laisser vide pour conserver le mot de passe actuel.',
      },
      {
        key: 'statut', label: 'Statut', type: 'select',
        options: [{ value: 'actif', label: 'Actif' }, { value: 'inactif', label: 'Inactif' }],
      },
    ],
  },

  periodes: {
    key: 'periodes',
    endpoint: 'periodes',
    titre: 'Périodes scolaires',
    titreSingulier: 'une période',
    eyebrow: 'Pédagogie',
    description: 'Trimestres ou semestres de l’année. Les évaluations et les bulletins s’y rattachent.',
    columns: [
      { key: 'libelle', label: 'Période' },
      { key: 'annee_libelle', label: 'Année scolaire' },
      { key: 'date_debut', label: 'Début', type: 'date' },
      { key: 'date_fin', label: 'Fin', type: 'date' },
    ],
    fields: [
      { key: 'id_annee_scolaire', label: 'Année scolaire', type: 'select', required: true, optionsEndpoint: 'annees-scolaires', optionsLabelKey: 'libelle' },
      { key: 'libelle', label: 'Libellé', type: 'text', required: true, placeholder: 'Trimestre 1' },
      { key: 'date_debut', label: 'Début', type: 'date', required: true },
      { key: 'date_fin', label: 'Fin', type: 'date', required: true },
    ],
  },

  coefficients: {
    key: 'coefficients',
    endpoint: 'coefficients',
    titre: 'Coefficients',
    titreSingulier: 'un coefficient',
    eyebrow: 'Pédagogie',
    description: 'Poids de chaque matière dans une classe. C’est la base du calcul des moyennes et des bulletins.',
    columns: [
      { key: 'matiere_nom', label: 'Matière' },
      { key: 'classe_nom', label: 'Classe' },
      { key: 'coefficient', label: 'Coefficient', type: 'number' },
    ],
    fields: [
      { key: 'id_matiere', label: 'Matière', type: 'select', required: true, optionsEndpoint: 'matieres', optionsLabelKey: 'nom' },
      { key: 'id_classe', label: 'Classe', type: 'select', required: true, optionsEndpoint: 'classes', optionsLabelKey: 'nom' },
      { key: 'id_annee_scolaire', label: 'Année scolaire', type: 'select', required: true, optionsEndpoint: 'annees-scolaires', optionsLabelKey: 'libelle' },
      { key: 'coefficient', label: 'Coefficient', type: 'number', required: true, placeholder: '4' },
    ],
  },

  affectations: {
    key: 'affectations',
    endpoint: 'affectations',
    titre: 'Affectations',
    titreSingulier: 'une affectation',
    eyebrow: 'Pédagogie',
    description: 'Qui enseigne quelle matière, dans quelle classe, et pour quel volume horaire.',
    columns: [
      { key: 'enseignant_matricule', label: 'Enseignant' },
      { key: 'matiere_nom', label: 'Matière' },
      { key: 'classe_nom', label: 'Classe' },
      { key: 'volume_horaire', label: 'Heures', type: 'number' },
    ],
    fields: [
      { key: 'id_enseignant', label: 'Enseignant', type: 'select', required: true, optionsEndpoint: 'enseignants', optionsLabelKey: 'matricule' },
      { key: 'id_matiere', label: 'Matière', type: 'select', required: true, optionsEndpoint: 'matieres', optionsLabelKey: 'nom' },
      { key: 'id_classe', label: 'Classe', type: 'select', required: true, optionsEndpoint: 'classes', optionsLabelKey: 'nom' },
      { key: 'id_annee_scolaire', label: 'Année scolaire', type: 'select', required: true, optionsEndpoint: 'annees-scolaires', optionsLabelKey: 'libelle' },
      { key: 'volume_horaire', label: 'Volume horaire hebdomadaire', type: 'number' },
    ],
  },

  bulletins: {
    key: 'bulletins',
    endpoint: 'bulletins',
    titre: 'Bulletins',
    titreSingulier: '',
    eyebrow: 'Pédagogie',
    description: 'Bulletins générés par période. Les moyennes proviennent des notes et des coefficients saisis.',
    lectureSeule: true,
    columns: [
      { key: 'eleve_nom', label: 'Élève' },
      { key: 'classe_nom', label: 'Classe' },
      { key: 'periode_libelle', label: 'Période' },
      { key: 'moyenne_generale', label: 'Moyenne', type: 'number' },
      { key: 'rang_classe', label: 'Rang', type: 'number' },
      { key: 'date_generation', label: 'Généré le', type: 'date' },
    ],
    fields: [],
  },

  recus: {
    key: 'recus',
    endpoint: 'recus',
    titre: 'Reçus',
    titreSingulier: '',
    eyebrow: 'Gestion',
    description: 'Reçus émis automatiquement à chaque encaissement. Consultation seule : un reçu ne se modifie pas.',
    lectureSeule: true,
    columns: [
      { key: 'numero_recu', label: 'Numéro' },
      { key: 'eleve_nom', label: 'Élève' },
      { key: 'montant', label: 'Montant', type: 'number' },
      { key: 'methode', label: 'Méthode' },
      { key: 'date_emission', label: 'Émis le', type: 'date' },
    ],
    fields: [],
  },

  'journal-activite': {
    key: 'journal-activite',
    endpoint: 'journal-activite',
    titre: 'Journal d’activité',
    titreSingulier: '',
    eyebrow: 'Administration',
    description: 'Trace des actions réalisées dans l’application : connexions, créations, modifications, suppressions et exports. Distinct du journal de caisse.',
    lectureSeule: true,
    columns: [
      { key: 'date_action', label: 'Date', type: 'date' },
      { key: 'utilisateur_nom', label: 'Utilisateur' },
      { key: 'action_libelle', label: 'Action', type: 'badge' },
      { key: 'entite', label: 'Objet' },
      { key: 'description', label: 'Détail' },
      { key: 'adresse_ip', label: 'Adresse IP' },
    ],
    fields: [],
  },
};
