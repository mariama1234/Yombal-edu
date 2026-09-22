import { Component, HostListener, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { HttpClient, HttpParams } from '@angular/common/http';
import { environment } from '../../../environments/environment';
import { FactureEleve, HistoriqueCaisse, MethodePaiement, Soldes } from '../../core/models/models';
import { AuthService } from '../../core/services/auth.service';
import { ToastService } from '../../core/services/toast.service';
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
  idEleve: number;
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

/** « 2026-10-05 », au jour du navigateur et non en UTC. */
function isoLocal(jour: Date): string {
  const mois = String(jour.getMonth() + 1).padStart(2, '0');
  const quantieme = String(jour.getDate()).padStart(2, '0');
  return `${jour.getFullYear()}-${mois}-${quantieme}`;
}

/**
 * Caisse & trésorerie : la journée de la caissière sur un seul écran.
 *
 * Trois questions, dans cet ordre : qu'est-ce qui est entré, qu'est-ce qui est
 * sorti, que doit contenir le tiroir. D'où les trois compteurs en tête, les
 * encaissements et les dépenses côte à côte, et la clôture en bas — la
 * ventilation par moyen de paiement est exactement ce qu'on recopie sur le
 * cahier avant de compter.
 *
 * L'encaissement lui-même reste un guichet : on saisit le matricule de la
 * facture, on coche ce qui est réglé, on imprime le reçu. Il s'ouvre en
 * fenêtre plutôt que sur une autre page, pour ne pas perdre la journée de vue.
 *
 * Un caissier ne voit que ses propres opérations : c'est le serveur qui le
 * décide, l'écran n'a pas à le savoir.
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
  private toast = inject(ToastService);
  private auth = inject(AuthService);
  private route = inject(ActivatedRoute);
  private router = inject(Router);

  readonly methodes = METHODES;
  readonly aujourdhui = isoLocal(new Date());

  // -- La journée ---------------------------------------------------------
  journee = signal<HistoriqueCaisse | null>(null);
  soldes = signal<Soldes | null>(null);
  jour = signal(this.aujourdhui);
  caissier = signal('');
  chargement = signal(true);

  // -- Le guichet ---------------------------------------------------------
  guichetOuvert = signal(false);
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

  // -- La clôture ---------------------------------------------------------
  clotureOuverte = signal(false);
  commentaireCloture = '';

  /** Un caissier ne voit que sa caisse ; les autres profils peuvent filtrer par caissier. */
  voitToutesLesCaisses = computed(() => this.auth.currentUser()?.role !== 'caissier');
  peutEncaisser = computed(() => this.auth.peutEcrire('encaissement'));
  peutSaisirDepense = computed(() => this.auth.peutEcrire('depenses'));
  estAujourdhui = computed(() => this.jour() >= this.aujourdhui);
  cloture = computed(() => this.journee()?.cloture ?? null);
  /** Des opérations ont été saisies après l'arrêté : la caisse ne tombe plus juste. */
  ecart = computed(() => {
    const c = this.cloture();
    if (!c) return 0;
    return Number(c.ecart_encaisse ?? 0) + Number(c.ecart_depense ?? 0);
  });

  ngOnInit(): void {
    const params = this.route.snapshot.queryParamMap;
    if (params.get('date')) this.jour.set(params.get('date')!);
    if (params.get('caissier')) this.caissier.set(params.get('caissier')!);
    this.charger();
    this.finance.soldes().subscribe({ next: (s) => this.soldes.set(s), error: () => {} });

    // Depuis l'inscription : la caisse s'ouvre sur l'élève à encaisser.
    const matricule = params.get('matricule');
    if (matricule && this.peutEncaisser()) {
      this.matricule = matricule;
      this.guichetOuvert.set(true);
      this.rechercher();
    }
  }

  /** Échap ferme le guichet, comme le bouton ✕. */
  @HostListener('document:keydown.escape')
  surEchap(): void {
    if (this.guichetOuvert()) this.fermerGuichet();
    else if (this.clotureOuverte()) this.clotureOuverte.set(false);
  }

  // -- Journée ------------------------------------------------------------
  charger(): void {
    this.chargement.set(true);
    // L'adresse garde le jour consulté : un lien partagé rouvre le même.
    this.router.navigate([], {
      relativeTo: this.route,
      queryParams: { date: this.jour(), caissier: this.caissier() || null, matricule: null },
      replaceUrl: true,
    });
    this.finance.historiqueCaisse({ date: this.jour(), caissier: this.caissier() }).subscribe({
      next: (h) => {
        this.journee.set(h);
        this.chargement.set(false);
      },
      error: (e) => {
        this.erreur.set(this.message(e, "La journée n'a pas pu être chargée."));
        this.chargement.set(false);
      },
    });
  }

  choisirJour(valeur: string): void {
    if (!valeur) return;
    this.jour.set(valeur);
    this.charger();
  }

  decaler(jours: number): void {
    const [annee, mois, quantieme] = this.jour().split('-').map(Number);
    this.choisirJour(isoLocal(new Date(annee, mois - 1, quantieme + jours)));
  }

  choisirCaissier(valeur: string): void {
    this.caissier.set(valeur);
    this.charger();
  }

  // -- Guichet ------------------------------------------------------------
  ouvrirGuichet(): void {
    this.guichetOuvert.set(true);
    this.clotureOuverte.set(false);
    this.erreur.set(null);
    this.recu.set(null);
    this.facture.set(null);
    this.matricule = '';
    this.montant = null;
    this.reference = '';
    this.fraisCoches.set([]);
  }

  fermerGuichet(): void {
    this.guichetOuvert.set(false);
    this.erreur.set(null);
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
          this.erreur.set(this.message(e, 'Recherche impossible.'));
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
            idEleve: f.id_eleve,
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
          this.toast.succes('Paiement enregistré',
                            `${f.prenom} ${f.nom} — reçu n° ${premier?.numero_recu ?? '—'}`);
          const inscrit = r.inscription_confirmee;
          if (inscrit) {
            this.toast.succes(`🎉 ${inscrit.eleve} est maintenant inscrit`,
                              `Droits d'inscription et premier mois réglés${inscrit.classe ? ' — ' + inscrit.classe : ''}.`);
          }
          this.facture.set(null);
          this.montant = null;
          this.reference = '';
          this.fraisCoches.set([]);
          this.occupe.set(false);
          // La journée se met à jour derrière la fenêtre : le paiement doit
          // apparaître dans la liste et dans les compteurs sans rechargement.
          if (this.estAujourdhui()) this.charger();
        },
        error: (e) => {
          this.erreur.set(this.message(e, "L'encaissement a échoué."));
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

  telechargerRecuDuJour(idRecu: number): void {
    this.finance.telecharger(`/recus/${idRecu}/pdf/`).subscribe({
      error: () => this.toast.erreur("Le reçu n'a pas pu être téléchargé."),
    });
  }

  // -- Clôture ------------------------------------------------------------
  cloturer(): void {
    this.occupe.set(true);
    this.erreur.set(null);
    this.finance
      .cloturerCaisse({ date: this.jour(), commentaire: this.commentaireCloture || null })
      .subscribe({
        next: (h) => {
          this.journee.set(h);
          this.clotureOuverte.set(false);
          this.commentaireCloture = '';
          this.occupe.set(false);
          this.toast.succes('Journée clôturée',
                            `${Number(h.cloture?.especes_attendues ?? 0).toLocaleString('fr-FR')} F attendus en espèces.`);
        },
        error: (e) => {
          this.occupe.set(false);
          this.toast.erreur('Clôture impossible', this.message(e, 'La clôture a échoué.'));
        },
      });
  }

  private message(erreur: unknown, defaut: string): string {
    const corps = (erreur as { error?: Record<string, unknown> })?.error;
    if (!corps) return defaut;
    const premiere = Object.values(corps)[0];
    const texte = Array.isArray(premiere) ? String(premiere[0]) : String(premiere);
    return texte || defaut;
  }
}
