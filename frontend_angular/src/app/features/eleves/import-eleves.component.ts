import { Component, computed, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { HttpErrorResponse } from '@angular/common/http';
import { EleveService } from './eleve.service';
import { ApercuImport, LigneImport } from '../../core/models/models';

type Etape = 'choix' | 'apercu' | 'termine';
type Filtre = '' | 'creation' | 'doublon' | 'ignore';

@Component({
  selector: 'app-import-eleves',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './import-eleves.component.html',
})
export class ImportElevesComponent {
  /**
   * L'import se fait en deux temps : on montre d'abord ce qui sera créé, on
   * n'écrit qu'après confirmation. Une liste d'école, c'est plusieurs
   * centaines d'élèves — se tromper de fichier sans filet coûterait cher à
   * défaire.
   */
  etape = signal<Etape>('choix');
  fichier = signal<File | null>(null);
  classeForcee = signal('');
  apercu = signal<ApercuImport | null>(null);
  chargement = signal(false);
  erreur = signal<string | null>(null);
  filtre = signal<Filtre>('');

  lignesVisibles = computed<LigneImport[]>(() => {
    const plan = this.apercu();
    if (!plan) {
      return [];
    }
    const filtre = this.filtre();
    return filtre ? plan.eleves.filter((e) => e.decision === filtre) : plan.eleves;
  });

  constructor(private eleveService: EleveService) {}

  choisirFichier(event: Event): void {
    const input = event.target as HTMLInputElement;
    this.fichier.set(input.files?.length ? input.files[0] : null);
    this.apercu.set(null);
    this.erreur.set(null);
    this.etape.set('choix');
  }

  analyser(): void {
    const fichier = this.fichier();
    if (!fichier) {
      return;
    }
    this.lancer(fichier, false, (plan) => {
      this.apercu.set(plan);
      this.etape.set('apercu');
    });
  }

  valider(): void {
    const fichier = this.fichier();
    if (!fichier) {
      return;
    }
    this.lancer(fichier, true, (plan) => {
      this.apercu.set(plan);
      this.etape.set('termine');
    });
  }

  recommencer(): void {
    this.etape.set('choix');
    this.fichier.set(null);
    this.apercu.set(null);
    this.erreur.set(null);
    this.filtre.set('');
  }

  private lancer(fichier: File, valider: boolean, succes: (plan: ApercuImport) => void): void {
    this.chargement.set(true);
    this.erreur.set(null);
    this.eleveService.importer(fichier, this.classeForcee().trim(), valider).subscribe({
      next: (plan) => {
        this.chargement.set(false);
        succes(plan);
      },
      error: (err: HttpErrorResponse) => {
        this.chargement.set(false);
        this.erreur.set(this.messageErreur(err));
      },
    });
  }

  /** Le serveur renvoie ses refus champ par champ ; on affiche le premier. */
  private messageErreur(err: HttpErrorResponse): string {
    const corps = err.error;
    if (typeof corps === 'string') {
      return corps;
    }
    if (corps && typeof corps === 'object') {
      const premier = Object.values(corps)[0];
      if (Array.isArray(premier)) {
        return String(premier[0]);
      }
      if (premier) {
        return String(premier);
      }
    }
    if (err.status === 403) {
      return "Seul un administrateur peut importer une liste d'élèves.";
    }
    return "L'import a échoué. Vérifiez le fichier et réessayez.";
  }

  libelleDecision(decision: LigneImport['decision']): string {
    return { creation: 'À créer', doublon: 'Déjà inscrit', ignore: 'Ignorée' }[decision];
  }

  classeDecision(decision: LigneImport['decision']): string {
    return { creation: 'actif', doublon: 'partiel', ignore: 'radie' }[decision];
  }
}
