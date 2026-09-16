import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { HistoriqueCaisse } from '../../core/models/models';
import { AuthService } from '../../core/services/auth.service';
import { FinanceService } from './finance.service';

/** « 2026-10-05 », au jour du navigateur et non en UTC. */
function isoLocal(jour: Date): string {
  const mois = String(jour.getMonth() + 1).padStart(2, '0');
  const quantieme = String(jour.getDate()).padStart(2, '0');
  return `${jour.getFullYear()}-${mois}-${quantieme}`;
}

/**
 * Historique de caisse, jour par jour.
 *
 * La question du soir : qu'est-ce qui est entré, qu'est-ce qui est sorti, et
 * qui l'a saisi ? Les paiements portent le caissier qui les a encaissés, les
 * dépenses celui qui les a enregistrées ; les totaux par caissier permettent
 * de rapprocher chaque tiroir de ce que dit le logiciel.
 *
 * Un administrateur voit toute la caisse et peut isoler un caissier ; un
 * caissier ne voit que ses propres opérations — c'est le serveur qui l'impose.
 */
@Component({
  selector: 'app-historique-caisse',
  standalone: true,
  imports: [CommonModule, RouterLink],
  templateUrl: './historique-caisse.component.html',
})
export class HistoriqueCaisseComponent implements OnInit {
  private finance = inject(FinanceService);
  private auth = inject(AuthService);
  private route = inject(ActivatedRoute);
  private router = inject(Router);

  historique = signal<HistoriqueCaisse | null>(null);
  chargement = signal(true);
  erreur = signal<string | null>(null);
  telechargement = signal<number | null>(null);

  readonly aujourdhui = isoLocal(new Date());
  jour = signal(this.aujourdhui);
  caissier = signal('');

  estAdmin = computed(() => this.auth.currentUser()?.role === 'admin');
  estAujourdhui = computed(() => this.jour() >= this.aujourdhui);

  ngOnInit(): void {
    const params = this.route.snapshot.queryParamMap;
    if (params.get('date')) this.jour.set(params.get('date')!);
    if (params.get('caissier')) this.caissier.set(params.get('caissier')!);
    this.charger();
  }

  charger(): void {
    this.chargement.set(true);
    this.erreur.set(null);
    // L'adresse garde le jour consulté : un retour arrière ou un lien partagé rouvre le même.
    this.router.navigate([], {
      relativeTo: this.route,
      queryParams: { date: this.jour(), caissier: this.caissier() || null },
      replaceUrl: true,
    });
    this.finance.historiqueCaisse({ date: this.jour(), caissier: this.caissier() }).subscribe({
      next: (h) => {
        this.historique.set(h);
        this.chargement.set(false);
      },
      error: (e) => {
        const corps = e?.error;
        this.erreur.set(corps?.date ?? corps?.caissier ?? corps?.detail ?? "L'historique n'a pas pu être chargé.");
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

  telechargerRecu(idRecu: number): void {
    this.telechargement.set(idRecu);
    this.finance.telecharger(`/recus/${idRecu}/pdf/`).subscribe({
      next: () => this.telechargement.set(null),
      error: () => {
        this.erreur.set("Le reçu n'a pas pu être téléchargé.");
        this.telechargement.set(null);
      },
    });
  }
}
