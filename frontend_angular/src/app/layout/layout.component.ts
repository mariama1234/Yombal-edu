import { Component, OnInit, computed, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { AuthService } from '../core/services/auth.service';
import { AnneeScolaireService } from '../core/services/annee-scolaire.service';
import { RechercheGlobaleComponent } from './recherche-globale.component';

@Component({
  selector: 'app-layout',
  standalone: true,
  imports: [CommonModule, RouterLink, RouterLinkActive, RouterOutlet,
            RechercheGlobaleComponent],
  templateUrl: './layout.component.html',
  styleUrl: './layout.component.scss',
})
export class LayoutComponent implements OnInit {
  // Public : le sélecteur d'année du bandeau haut lit et écrit directement
  // dedans, et toutes les listes s'y accordent.
  public annees = inject(AnneeScolaireService);

  constructor(public auth: AuthService) {}

  ngOnInit(): void {
    this.annees.charger();
  }

  initiales = computed(() => {
    const u = this.auth.currentUser();
    if (!u) return '';
    return `${u.prenom[0] ?? ''}${u.nom[0] ?? ''}`.toUpperCase();
  });

  changerAnnee(valeur: string): void {
    this.annees.definir(valeur ? Number(valeur) : null);
  }

  logout(): void {
    this.auth.logout();
  }
}
