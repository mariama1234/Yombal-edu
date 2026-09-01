import { Component, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute } from '@angular/router';

interface PlaceholderData {
  eyebrow: string;
  title: string;
  lead: string;
  note: string;
}

const DATA: Record<string, PlaceholderData> = {
  communications: {
    eyebrow: 'Gestion', title: 'Communications',
    lead: 'Messages aux familles, annonces internes et campagnes ciblées.',
    note: "Ce module n'a pas encore de table dédiée dans le schéma validé — il faudra définir un modèle Message (destinataires, canal, contenu, statut d'envoi) avant de le rendre fonctionnel.",
  },
  documents: {
    eyebrow: 'Gestion', title: 'Documents & rapports',
    lead: 'Bulletins, listes administratives et états financiers exportables.',
    note: "Les données existent déjà (bulletins, frais, dépenses) — ce module reste à construire comme couche d'export (PDF/Excel) au-dessus de l'API existante, pas comme nouvelle table.",
  },
  parametres: {
    eyebrow: 'Administration', title: 'Paramètres',
    lead: 'Établissement, utilisateurs, rôles et préférences de notification.',
    note: "Le modèle Ecole existe déjà côté backend (/api/ecoles/) — il manque l'écran de gestion des utilisateurs et rôles pour compléter ce module.",
  },
  'journal-activite': {
    eyebrow: 'Administration', title: "Journal d'activité",
    lead: 'Historique des actions réalisées dans l\u2019espace administrateur.',
    note: "Distinct du journal de caisse (déjà fonctionnel) — un vrai journal d'audit nécessite une table dédiée (utilisateur, action, date, objet concerné) à ajouter au schéma si ce suivi est prioritaire.",
  },
};

@Component({
  selector: 'app-placeholder',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './placeholder.component.html',
})
export class PlaceholderComponent implements OnInit {
  data = signal<PlaceholderData | null>(null);

  constructor(private route: ActivatedRoute) {}

  ngOnInit(): void {
    this.route.data.subscribe((d) => this.data.set(DATA[d['key']]));
  }
}
