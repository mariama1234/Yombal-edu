import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { HttpClient, HttpParams } from '@angular/common/http';
import { environment } from '../../../environments/environment';
import { FactureEleve, MethodePaiement } from '../../core/models/models';
import { FinanceService } from './finance.service';

const METHODES: { value: MethodePaiement; label: string }[] = [
  { value: 'especes', label: 'Espèces' },
  { value: 'orange_money', label: 'Orange Money' },
  { value: 'wave', label: 'Wave' },
  { value: 'free_money', label: 'Free Money' },
  { value: 'carte_bancaire', label: 'Carte bancaire' },
];

/** Ce qui s'affiche après l'encaissement, et que le PDF reprend. */
interface RecuGuichet {
  numero: string;
  matricule: string;
  nom: string;
  prenom: string;
  classe: string | null;
  date: string;
  montant: number;
  reste: number;
  statut: string | null;
  /** Paiements créés par l'encaissement, pour éditer le reçu. */
  ids: string;
}

/**
 * La caisse : du matricule de la facture au reçu.
 *
 * La caissière saisit le matricule, l'écran affiche l'élève, sa classe et ce
 * qu'il doit ; elle saisit le montant payé, et le serveur l'impute — frais
 * d'inscription, puis mensualité, puis frais cochés. Le reçu reprend le
 * montant payé et le montant restant.
 */
@Component({
  selector: 'app-caisse',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './caisse.component.html',
})
export class CaisseComponent implements OnInit {
  private http = inject(HttpClient);
  private finance = inject(FinanceService);
  private route = inject(ActivatedRoute);

  readonly methodes = METHODES;

  matricule = '';
  montant: number | null = null;
  methode: MethodePaiement = 'especes';
  reference = '';

  facture = signal<FactureEleve | null>(null);
  /** Lignes de la facture cochées : le paiement ne va que sur elles. */
  fraisCoches = signal<number[]>([]);
  recu = signal<RecuGuichet | null>(null);
  erreur = signal<string | null>(null);
  occupe = signal(false);

  ngOnInit(): void {
    // Depuis l'écran d'inscription : la caisse s'ouvre sur l'élève.
    const matricule = this.route.snapshot.queryParamMap.get('matricule');
    if (matricule) {
      this.matricule = matricule;
      this.rechercher();
    }
  }

  rechercher(): void {
    const matricule = this.matricule.trim();
    if (!matricule) {
      this.erreur.set('Saisissez le matricule figurant sur la facture.');
      return;
    }
    this.occupe.set(true);
    this.erreur.set(null);
    this.recu.set(null);
    this.facture.set(null);
    this.http
      .get<FactureEleve>(`${environment.apiUrl}/eleves/par-matricule/`, {
        params: new HttpParams().set('matricule', matricule),
      })
      .subscribe({
        next: (f) => {
          this.facture.set(f);
          this.montant = null;
          this.fraisCoches.set([]);
          this.occupe.set(false);
        },
        error: (e) => {
          this.erreur.set(e?.error?.detail ?? e?.error?.matricule?.[0] ?? 'Recherche impossible.');
          this.occupe.set(false);
        },
      });
  }

  encaisser(): void {
    const f = this.facture();
    if (!f) return;
    if (!this.montant || this.montant <= 0) {
      this.erreur.set('Saisissez le montant payé.');
      return;
    }
    this.occupe.set(true);
    this.erreur.set(null);
    this.finance
      .encaisserPourEleve({
        id_eleve: f.id_eleve,
        id_annee_scolaire: f.id_annee_scolaire,
        montant: String(this.montant),
        methode: this.methode,
        reference_transaction: this.reference || null,
        frais: this.fraisCoches().length ? this.fraisCoches() : null,
      })
      .subscribe({
        next: (r) => {
          const premier = r.paiements[0];
          this.recu.set({
            numero: premier?.numero_recu ?? '—',
            matricule: f.matricule,
            nom: f.nom,
            prenom: f.prenom,
            classe: f.classe,
            date: premier?.date_paiement ?? new Date().toISOString(),
            montant: r.paiements.reduce((somme, p) => somme + Number(p.montant), 0),
            reste: Number(r.situation.resume.reste),
            statut: r.situation.confirmation?.statut_libelle ?? null,
            ids: r.paiements.map((p) => p.id).join(','),
          });
          this.facture.set(null);
          this.montant = null;
          this.reference = '';
          this.fraisCoches.set([]);
          this.occupe.set(false);
        },
        error: (e) => {
          this.erreur.set(e?.error?.detail ?? e?.error?.montant?.[0] ?? "L'encaissement a échoué.");
          this.occupe.set(false);
        },
      });
  }

  /** Cocher une ligne propose d'en régler le reste : le montant suit les cases. */
  basculerFrais(id: number): void {
    this.fraisCoches.update((ids) => (ids.includes(id) ? ids.filter((i) => i !== id) : [...ids, id]));
    const lignes = this.facture()?.lignes ?? [];
    const total = lignes
      .filter((l) => this.fraisCoches().includes(l.id))
      .reduce((somme, l) => somme + Number(l.reste), 0);
    this.montant = this.fraisCoches().length ? total : null;
  }

  imprimerRecu(): void {
    const r = this.recu();
    if (!r) return;
    this.finance
      .telecharger('/paiements/recu/', { ids: r.ids })
      .subscribe({ error: () => this.erreur.set("Le reçu n'a pas pu être généré.") });
  }

  nouvelEncaissement(): void {
    this.recu.set(null);
    this.facture.set(null);
    this.matricule = '';
    this.erreur.set(null);
  }
}
