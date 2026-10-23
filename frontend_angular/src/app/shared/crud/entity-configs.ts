import { EntityConfig } from './crud-config.model';

/**
 * Une configuration par module. Pour ajouter un nouveau module au menu,
 * il suffit d'ajouter une entrée ici — aucun composant à créer.
 */
export const ENTITY_CONFIGS: Record<string, EntityConfig> = {

  // Les référentiels de structure viennent en premier : ce sont eux qu'il
  // faut poser avant toute inscription. Sans année scolaire ni niveau, on ne
  // peut pas créer une classe, donc pas inscrire un élève.
  'annees-scolaires': {
    key: 'annees-scolaires',
    suite: [
      { label: 'Niveaux', chemin: '/niveaux', module: 'classes' },
      { label: 'Périodes scolaires', chemin: '/periodes', module: 'annees_scolaires' },
    ],
    endpoint: 'annees-scolaires',
    titre: 'Années scolaires',
    titreSingulier: 'une année scolaire',
    eyebrow: 'Organisation scolaire',
    description:
      'Ouvre une nouvelle année, ou archive celle qui vient de s’achever. Classes, '
      + 'inscriptions, notes et frais sont tous rattachés à une année : c’est ce qui '
      + 'permet de conserver les résultats des années passées sans les mélanger.',
    columns: [
      { key: 'libelle', label: 'Année' },
      { key: 'date_debut', label: 'Début', type: 'date' },
      { key: 'date_fin', label: 'Fin', type: 'date' },
      { key: 'statut', label: 'Statut', type: 'badge' },
    ],
    fields: [
      {
        key: 'libelle', label: 'Libellé', type: 'text', required: true, placeholder: '2026-2027',
        aide: 'Deux années qui se suivent : le premier jour tombe dans la première, '
            + 'le dernier dans la seconde. Deux années de l’école ne se chevauchent pas.',
      },
      { key: 'date_debut', label: 'Premier jour', type: 'date', required: true },
      { key: 'date_fin', label: 'Dernier jour', type: 'date', required: true },
      {
        key: 'statut', label: 'Statut', type: 'select', required: true,
        aide: 'Une seule année est active à la fois : activer celle-ci archive '
            + 'automatiquement les autres. C’est l’année que les écrans proposent '
            + 'par défaut ; les archivées restent consultables par le filtre en '
            + 'haut de page.',
        options: [
          { value: 'active', label: 'Active (année en cours)' },
          { value: 'archivee', label: 'Archivée' },
        ],
      },
    ],
  },

  niveaux: {
    key: 'niveaux',
    suite: [
      { label: 'Classes', chemin: '/classes', module: 'classes' },
      { label: 'Tarifs par niveau', chemin: '/finances/tarifs', module: 'types_frais' },
    ],
    endpoint: 'niveaux',
    titre: 'Niveaux',
    titreSingulier: 'un niveau',
    eyebrow: 'Organisation scolaire',
    description:
      'Les niveaux d’enseignement de l’école — CI, CP, CE1, 6ème… Une classe se '
      + 'rattache à un niveau, qui sert aussi à filtrer les listes.',
    columns: [
      { key: 'libelle', label: 'Niveau' },
      { key: 'age_min', label: 'Âge min.', type: 'number' },
      { key: 'age_max', label: 'Âge max.', type: 'number' },
    ],
    fields: [
      {
        key: 'libelle', label: 'Libellé du niveau', type: 'text', required: true, placeholder: '6ème',
        aide: 'Les montants des frais pour ce niveau se règlent dans Finances → Tarifs par niveau.',
      },
      {
        key: 'age_min', label: 'Âge minimal (ans)', type: 'number', min: 0,
        aide: 'Âge de l’élève à la date de référence de l’année (31/12 par défaut, '
          + 'réglable dans Paramètres). Vide : pas de limite. Un niveau connu (CI, 6ème…) '
          + 'est prérempli à sa création.',
      },
      { key: 'age_max', label: 'Âge maximal (ans)', type: 'number', min: 0 },
    ],
  },

  salles: {
    key: 'salles',
    suite: [
      { label: 'Emploi du temps', chemin: '/emploi-du-temps', module: 'emploi_du_temps' },
    ],
    endpoint: 'salles',
    titre: 'Salles',
    titreSingulier: 'une salle',
    eyebrow: 'Organisation scolaire',
    description:
      'Les salles de cours, avec leur capacité. L’emploi du temps refuse d’y placer '
      + 'deux séances sur le même créneau.',
    columns: [
      { key: 'nom', label: 'Salle' },
      { key: 'capacite', label: 'Capacité', type: 'number' },
    ],
    fields: [
      { key: 'nom', label: 'Nom de la salle', type: 'text', required: true, placeholder: 'Salle 1' },
      { key: 'capacite', label: 'Capacité (places)', type: 'number', min: 1 },
    ],
  },

  'conseils-classe': {
    key: 'conseils-classe',
    suite: [
      { label: 'Bulletins', chemin: '/pedagogie/bulletins', module: 'bulletins' },
    ],
    filtreParAnnee: true,
    endpoint: 'conseils-classe',
    titre: 'Conseils de classe',
    titreSingulier: 'un conseil de classe',
    eyebrow: 'Pédagogie',
    description:
      'Date et décisions du conseil, par classe et par période. La décision saisie ici '
      + 'est reprise en bas des bulletins de la période.',
    columns: [
      { key: 'date_conseil', label: 'Date', type: 'date' },
      { key: 'decisions', label: 'Décisions' },
    ],
    fields: [
      { key: 'id_classe', label: 'Classe', type: 'select', required: true, optionsEndpoint: 'classes', optionsLabelKey: 'nom' },
      { key: 'id_periode', label: 'Période', type: 'select', required: true, optionsEndpoint: 'periodes', optionsLabelKey: 'libelle' },
      { key: 'date_conseil', label: 'Date du conseil', type: 'date', required: true },
      { key: 'decisions', label: 'Décisions', type: 'textarea', span2: true, placeholder: 'Passage en 5ème, félicitations…' },
    ],
  },

  parents: {
    key: 'parents',
    acces: { role: 'parent' },
    enfants: true,
    suite: [
      { label: 'Élèves', chemin: '/eleves', module: 'eleves' },
    ],
    endpoint: 'parents',
    titre: 'Parents & tuteurs',
    titreSingulier: 'un parent ou tuteur',
    eyebrow: 'Élèves & familles',
    description: 'Les parents et tuteurs des élèves. Un même parent peut avoir plusieurs enfants '
      + 'à l’école : ouvrez sa fiche pour les voir, ou lui en rattacher un autre.',
    columns: [
      { key: 'nom', label: 'Nom' },
      { key: 'prenom', label: 'Prénom' },
      { key: 'telephone', label: 'Téléphone' },
      { key: 'email', label: 'E-mail' },
      { key: 'enfants_noms', label: 'Enfants' },
    ],
    fields: [
      { key: 'nom', label: 'Nom', type: 'text', required: true },
      { key: 'prenom', label: 'Prénom', type: 'text', required: true },
      { key: 'profession', label: 'Profession', type: 'text' },
      { key: 'telephone', label: 'Téléphone', type: 'tel', required: true },
      { key: 'email', label: 'E-mail', type: 'email' },
      { key: 'adresse', label: 'Adresse', type: 'text', span2: true, placeholder: 'Quartier, rue, ville' },
    ],
  },

  matieres: {
    key: 'matieres',
    suite: [
      { label: 'Coefficients', chemin: '/coefficients', module: 'matieres' },
      { label: 'Affectations', chemin: '/affectations', module: 'matieres' },
    ],
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
    acces: { role: 'enseignant' },
    suite: [
      { label: 'Affectations', chemin: '/affectations', module: 'matieres' },
    ],
    endpoint: 'enseignants',
    titre: 'Enseignants & personnel',
    titreSingulier: 'un enseignant',
    eyebrow: 'Pédagogie',
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
    filtreParAnnee: true,
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
      // La classe se choisit d'abord : elle restreint la liste des élèves.
      { key: 'id_classe', label: 'Classe', type: 'select', required: true, optionsEndpoint: 'classes', optionsLabelKey: 'nom' },
      {
        key: 'id_eleve', label: 'Élève', type: 'select', required: true,
        optionsEndpoint: 'eleves', optionsLabelKey: 'nom_complet', optionsSecondaireKey: 'matricule',
        optionsFiltrePar: { champ: 'id_classe', param: 'classe' },
        aide: 'Les élèves inscrits dans la classe choisie.',
      },
      {
        key: 'statut', label: 'Type', type: 'select', required: true,
        options: [{ value: 'absent', label: 'Absent' }, { value: 'retard', label: 'Retard' }],
      },
      { key: 'date_absence', label: 'Date', type: 'date', required: true, defaut: 'aujourdhui', pasDansLeFutur: true },
      { key: 'justifiee', label: 'Absence justifiée', type: 'checkbox' },
      { key: 'motif', label: 'Motif', type: 'textarea', span2: true },
    ],
  },

  // Sans évaluation, aucune note ne peut être saisie : c'est le point
  // d'entrée du module pédagogique, avant la grille de saisie.
  evaluations: {
    key: 'evaluations',
    suite: [
      { label: 'Saisie des notes', chemin: '/pedagogie/notes', module: 'notes:ecriture' },
    ],
    filtreParAnnee: true,
    endpoint: 'evaluations',
    titre: 'Évaluations',
    titreSingulier: 'une évaluation',
    eyebrow: 'Pédagogie',
    description:
      'Devoirs, compositions et examens. Une fois l’évaluation créée, les notes se '
      + 'saisissent en grille depuis « Saisie des notes ».',
    columns: [
      { key: 'date_evaluation', label: 'Date', type: 'date' },
      { key: 'type', label: 'Type', type: 'badge' },
      { key: 'bareme', label: 'Barème', type: 'number' },
      { key: 'coefficient', label: 'Coefficient', type: 'number' },
    ],
    fields: [
      { key: 'id_matiere', label: 'Matière', type: 'select', required: true, optionsEndpoint: 'matieres', optionsLabelKey: 'nom' },
      { key: 'id_classe', label: 'Classe', type: 'select', required: true, optionsEndpoint: 'classes', optionsLabelKey: 'nom' },
      { key: 'id_periode', label: 'Période', type: 'select', required: true, optionsEndpoint: 'periodes', optionsLabelKey: 'libelle' },
      {
        key: 'type', label: 'Type', type: 'select', required: true,
        options: [
          { value: 'devoir', label: 'Devoir' },
          { value: 'composition', label: 'Composition' },
          { value: 'examen', label: 'Examen' },
        ],
      },
      { key: 'date_evaluation', label: "Date de l'épreuve", type: 'date', required: true },
      { key: 'bareme', label: 'Barème (note maximale)', type: 'number', required: true, placeholder: '20', min: 1 },
      { key: 'coefficient', label: 'Coefficient dans la matière', type: 'number', required: true, placeholder: '1', min: 0 },
    ],
  },

  notes: {
    key: 'notes',
    suite: [
      { label: 'Bulletins', chemin: '/pedagogie/bulletins', module: 'bulletins' },
    ],
    endpoint: 'notes',
    titre: 'Notes',
    titreSingulier: 'une note',
    eyebrow: 'Pédagogie',
    description:
      'Vue tableau des notes déjà saisies. Pour saisir une classe entière, passe par '
      + '« Saisie des notes » : la grille envoie tout en une fois.',
    columns: [
      { key: 'valeur', label: 'Note', type: 'number' },
      { key: 'appreciation', label: 'Appréciation' },
      { key: 'date_saisie', label: 'Saisie le', type: 'date' },
    ],
    fields: [
      {
        key: 'id_eleve', label: 'Élève', type: 'select', required: true,
        optionsEndpoint: 'eleves', optionsLabelKey: 'nom_complet', optionsSecondaireKey: 'matricule',
      },
      { key: 'id_evaluation', label: 'Évaluation', type: 'select', required: true, optionsEndpoint: 'evaluations', optionsLabelKey: 'type' },
      // Le barème appartient à l'évaluation : la note se saisit telle qu'elle
      // est portée sur la copie, la conversion sur 20 est faite au calcul.
      { key: 'valeur', label: 'Note (dans le barème de l’évaluation)', type: 'number', required: true, min: 0 },
      { key: 'appreciation', label: 'Appréciation', type: 'textarea', span2: true },
    ],
  },

  'emploi-du-temps': {
    key: 'emploi-du-temps',
    suite: [
      { label: 'Présences', chemin: '/presences', module: 'presences' },
    ],
    filtreParAnnee: true,
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
    suite: [
      { label: 'Inscrire un élève', chemin: '/eleves/nouveau', module: 'inscriptions:ecriture' },
      { label: 'Coefficients', chemin: '/coefficients', module: 'matieres' },
    ],
    liens: [
      { label: 'Élèves', chemin: '/eleves', parametre: 'classe', module: 'eleves' },
    ],
    filtreParAnnee: true,
    endpoint: 'classes',
    titre: 'Classes',
    titreSingulier: 'une classe',
    eyebrow: 'Organisation scolaire',
    filtres: [
      { param: 'id_niveau', tous: 'Tous les niveaux', optionsEndpoint: 'niveaux', optionsLabelKey: 'libelle', obligatoire: true },
    ],
    description: 'Les classes de l’année affichée en haut, rangées par niveau, avec leur effectif '
      + 'et leur capacité.',
    columns: [
      { key: 'nom', label: 'Classe' },
      { key: 'niveau_libelle', label: 'Niveau' },
      { key: 'effectif', label: 'Effectif', type: 'number' },
      { key: 'capacite_max', label: 'Capacité', type: 'number' },
    ],
    fields: [
      { key: 'nom', label: 'Nom de la classe', type: 'text', required: true, placeholder: '6ème A' },
      { key: 'id_niveau', label: 'Niveau (facultatif)', type: 'select', optionsEndpoint: 'niveaux', optionsLabelKey: 'libelle' },
      { key: 'id_annee_scolaire', label: 'Année scolaire', type: 'select', required: true, optionsEndpoint: 'annees-scolaires', optionsLabelKey: 'libelle' },
      { key: 'capacite_max', label: 'Capacité maximale (places)', type: 'number', required: true, min: 1 },
      { key: 'id_enseignant_principal', label: 'Professeur principal', type: 'select', optionsEndpoint: 'enseignants', optionsLabelKey: 'matricule' },
    ],
  },

  'types-frais': {
    key: 'types-frais',
    suite: [
      { label: 'Tarifs par niveau', chemin: '/finances/tarifs', module: 'types_frais' },
    ],
    endpoint: 'types-frais',
    titre: 'Types de frais',
    titreSingulier: 'un type de frais',
    eyebrow: 'Finances',
    description:
      'Les frais de votre établissement, sous le nom que vous leur donnez. La nature '
      + 'dit comment le logiciel les traite : les frais d’inscription et la mensualité '
      + 'sont facturés d’office à l’inscription, les autres frais se cochent.',
    columns: [
      { key: 'libelle', label: 'Frais' },
      { key: 'nature_libelle', label: 'Nature' },
      { key: 'montant_defaut', label: 'Montant par défaut', type: 'number' },
      { key: 'frequence', label: 'Fréquence' },
    ],
    fields: [
      {
        key: 'libelle', label: 'Nom du frais', type: 'text', required: true, span2: true,
        placeholder: 'Transport, Adiya, Cantine…',
      },
      {
        key: 'nature', label: 'Nature', type: 'select', required: true,
        aide: 'Une seule « Frais d’inscription » et une seule « Mensualité » par établissement.',
        options: [
          { value: 'autre', label: 'Autre frais (à cocher à l’inscription)' },
          { value: 'inscription', label: 'Frais d’inscription (dus d’office)' },
          { value: 'mensualite', label: 'Mensualité (due chaque mois)' },
        ],
      },
      {
        key: 'montant_defaut', label: 'Montant par défaut (F CFA)', type: 'number', required: true, min: 0,
        aide: 'Pour l’inscription et la mensualité, le tarif du niveau prime s’il est renseigné.',
      },
      {
        key: 'frequence', label: 'Fréquence de paiement', type: 'select',
        aide: 'Un frais mensuel est présenté et réglé mois par mois.',
        // Mêmes valeurs que `TypeFraisSerializer.FREQUENCES` côté serveur.
        options: [
          { value: 'Unique', label: 'Unique (une seule fois)' },
          { value: 'Mensuelle', label: 'Mensuelle (chaque mois)' },
          { value: 'Trimestrielle', label: 'Trimestrielle (chaque trimestre)' },
          { value: 'Annuelle', label: 'Annuelle (une fois par an)' },
        ],
      },
    ],
  },

  'types-depenses': {
    key: 'types-depenses',
    suite: [
      { label: 'Dépenses', chemin: '/depenses', module: 'depenses' },
      { label: 'Bilan financier', chemin: '/finances/bilan', module: 'budget' },
    ],
    endpoint: 'types-depenses',
    // Le budget disponible se compte sur l'année du bandeau haut : la liste
    // la transmet, sinon une enveloppe annuelle compterait toutes les années.
    filtreParAnnee: true,
    // « Budgets » dans le titre : c'est ici que l'enveloppe de chaque poste
    // se fixe, et le mot manquait au menu pour qu'on l'y cherche.
    titre: 'Catégories & budgets',
    titreSingulier: 'une catégorie',
    eyebrow: 'Finances',
    description: 'Postes de charge de l’école : loyer, salaires, fournitures, transport… Chacun porte '
      + 'l’enveloppe de l’année affichée en haut de l’écran, que les dépenses consomment au fur et à mesure. '
      + 'Changez d’année pour voir ou fixer celle d’un autre exercice.',
    // L'enveloppe, ce qu'elle a déjà payé, et ce qui reste : les trois se
    // lisent d'un coup d'œil, sur l'année affichée en haut. Le dépensé et le
    // disponible sont calculés par l'API à chaque affichage — une dépense
    // enregistrée les met donc à jour d'elle-même.
    columns: [
      { key: 'libelle', label: 'Catégorie' },
      { key: 'budget_alloue', label: 'Budget alloué', type: 'number' },
      { key: 'montant_depense', label: 'Déjà dépensé', type: 'number' },
      { key: 'budget_disponible', label: 'Budget disponible', type: 'number' },
    ],
    fields: [
      { key: 'libelle', label: 'Libellé', type: 'text', required: true, placeholder: 'Loyer' },
      {
        key: 'budget_alloue', label: 'Budget alloué (F CFA)', type: 'number', min: 0,
        aide: 'Enveloppe de ce poste pour l’année affichée en haut de l’écran — une autre année '
            + 'a la sienne. Le disponible ne se saisit pas : il se déduit des dépenses enregistrées.',
      },
    ],
  },

  depenses: {
    key: 'depenses',
    suite: [
      { label: 'Bilan financier', chemin: '/finances/bilan', module: 'budget' },
    ],
    endpoint: 'depenses',
    titre: 'Dépenses',
    titreSingulier: 'une dépense',
    eyebrow: 'Finances',
    description: 'Chaque sortie d’argent de l’école, rangée par catégorie : elle est débitée de la '
      + 'caisse et consomme le budget de sa catégorie.',
    columns: [
      { key: 'date_depense', label: 'Date', type: 'date' },
      { key: 'categorie', label: 'Catégorie' },
      { key: 'motif', label: 'Motif' },
      { key: 'montant', label: 'Montant', type: 'number' },
      { key: 'saisi_par', label: 'Saisie par' },
      { key: 'a_justificatif', label: 'Justificatif' },
    ],
    fields: [
      // La catégorie ouvre la saisie : c'est elle qui décide du budget, et le
      // disponible s'affiche à côté avant même qu'un montant soit saisi. Le
      // motif vient après les chiffres, une fois la dépense arrêtée.
      { key: 'id_type_depense', label: 'Catégorie', type: 'select', required: true, optionsEndpoint: 'types-depenses', optionsLabelKey: 'libelle' },
      {
        key: 'budget_disponible', label: 'Budget restant', type: 'lecture',
        source: {
          champ: 'id_type_depense', cle: 'budget_disponible', suffixe: 'F CFA',
          compare: 'montant', total: 'budget_alloue',
        },
        aide: 'Ce qui reste sur le budget de la catégorie pour l’année affichée en haut. '
            + 'Le montant saisi s’en retranche au fur et à mesure de la frappe.',
      },
      { key: 'montant', label: 'Montant (F CFA)', type: 'number', required: true, min: 1 },
      { key: 'date_depense', label: 'Date', type: 'date', required: true, defaut: 'aujourdhui', pasDansLeFutur: true },
      { key: 'motif', label: 'Motif', type: 'text', required: true, span2: true },
      {
        key: 'justificatif', label: 'Justificatif', type: 'fichier', span2: true,
        fichier: {
          chemin: 'justificatif', presente: 'a_justificatif',
          accept: 'application/pdf,image/jpeg,image/png,image/webp',
        },
        aide: 'Facture ou reçu : PDF ou photo (JPEG, PNG, WebP), 5 Mo au plus.',
      },
    ],
  },

  communications: {
    key: 'communications',
    endpoint: 'communications',
    titre: 'Communications',
    titreSingulier: 'une communication',
    eyebrow: 'Communication & documents',
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
          { value: 'admin', label: 'Administrateurs' }, { value: 'directeur', label: 'Directeurs' },
          { value: 'responsable_administratif', label: 'Responsables administratifs' },
          { value: 'surveillant', label: 'Surveillants' }, { value: 'secretaire', label: 'Secrétaires administratifs' },
          { value: 'caissier', label: 'Caissiers' }, { value: 'comptable', label: 'Comptables' },
          { value: 'responsable_financier', label: 'Responsables financiers' },
          { value: 'enseignant', label: 'Enseignants' },
          { value: 'parent', label: 'Parents' }, { value: 'eleve', label: 'Élèves' },
        ],
      },
    ],
  },

  documents: {
    key: 'documents',
    endpoint: 'documents',
    titre: 'Documents & rapports',
    titreSingulier: 'un document',
    eyebrow: 'Communication & documents',
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
      {
        key: 'id_eleve', label: 'Rattacher au dossier d’un élève', type: 'select',
        optionsEndpoint: 'eleves', optionsLabelKey: 'nom_complet', optionsSecondaireKey: 'matricule',
      },
    ],
  },

  parametres: {
    key: 'parametres',
    endpoint: 'parametres',
    // L'identité de l'école (nom, adresse, coordonnées) ne se règle pas en
    // clé/valeur : elle a son propre écran, rappelé ici pour qu'on le trouve.
    suite: [
      { label: 'Informations de l’école', chemin: '/parametres/ecole', module: 'parametres' },
    ],
    titre: 'Paramètres',
    titreSingulier: 'un paramètre',
    eyebrow: 'Administration',
    description: 'Réglages propres à l’établissement, en clé/valeur (devise, barème de notation, seuil de relance, en-tête des reçus...). '
      + 'Le nom, l’adresse et les coordonnées de l’école se modifient sur la fiche de l’établissement.',
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
        aide: 'Administrateur, directeur et responsable administratif ont les mêmes droits ; '
            + 'surveillant et secrétaire administratif aussi.',
        options: [
          { value: 'admin', label: 'Administrateur' }, { value: 'directeur', label: 'Directeur' },
          { value: 'responsable_administratif', label: 'Responsable administratif' },
          { value: 'surveillant', label: 'Surveillant' }, { value: 'secretaire', label: 'Secrétaire administratif' },
          { value: 'caissier', label: 'Caissier' }, { value: 'comptable', label: 'Comptable' },
          { value: 'responsable_financier', label: 'Responsable financier' },
          { value: 'enseignant', label: 'Enseignant' },
          { value: 'parent', label: 'Parent' }, { value: 'eleve', label: 'Élève' },
        ],
      },
      { key: 'telephone', label: 'Téléphone', type: 'tel' },
      {
        key: 'password', label: 'Mot de passe', type: 'password', span2: true,
        aide: 'Obligatoire à la création, 8 caractères minimum. En modification, '
            + 'laisser vide pour conserver le mot de passe actuel.',
      },
      {
        key: 'statut', label: 'Statut', type: 'select',
        options: [{ value: 'actif', label: 'Actif' }, { value: 'inactif', label: 'Inactif' }],
      },
    ],
  },

  periodes: {
    key: 'periodes',
    suite: [
      { label: 'Évaluations', chemin: '/evaluations', module: 'notes' },
    ],
    filtreParAnnee: true,
    endpoint: 'periodes',
    titre: 'Périodes scolaires',
    titreSingulier: 'une période',
    eyebrow: 'Organisation scolaire',
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
    suite: [
      { label: 'Évaluations', chemin: '/evaluations', module: 'notes' },
    ],
    filtreParAnnee: true,
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
      { key: 'coefficient', label: 'Coefficient', type: 'number', required: true, placeholder: '4', min: 0 },
    ],
  },

  affectations: {
    key: 'affectations',
    suite: [
      { label: 'Emploi du temps', chemin: '/emploi-du-temps', module: 'emploi_du_temps' },
    ],
    filtreParAnnee: true,
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
      { key: 'volume_horaire', label: 'Volume horaire hebdomadaire', type: 'number', min: 0 },
    ],
  },

  bulletins: {
    key: 'bulletins',
    filtreParAnnee: true,
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
    eyebrow: 'Finances',
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
