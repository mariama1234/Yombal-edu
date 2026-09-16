import { Injectable, signal } from '@angular/core';

export type GenreToast = 'succes' | 'info' | 'erreur';

export interface Toast {
  id: number;
  genre: GenreToast;
  titre: string;
  detail?: string;
  /** Durée d'affichage en millisecondes. */
  duree: number;
}

/** Au-delà, les plus anciennes cèdent la place : l'écran ne se couvre pas de bulles. */
const MAXIMUM = 4;

// Le temps de lire sans interrompre : une confirmation courte se lit en
// quelques secondes, un message d'erreur demande plus, et rien ne disparaît
// avant qu'on ait eu le temps de lever les yeux de la saisie.
const DUREES: Record<GenreToast, number> = { succes: 9000, info: 10000, erreur: 14000 };

/**
 * Les notifications éphémères (« toasts ») qui confirment un enregistrement.
 *
 * Deux sources. L'intercepteur HTTP pose un message générique après chaque
 * enregistrement réussi — « Enregistrement effectué » —, si bien qu'aucun
 * écran, présent ou futur, n'enregistre en silence. Un écran qui sait mieux
 * dire (« Ndalé FEDIOR est maintenant inscrit ») appelle `succes()` dans son
 * propre retour : le message générique, différé d'un tour, s'efface alors
 * devant lui au lieu de s'empiler.
 */
@Injectable({ providedIn: 'root' })
export class ToastService {
  readonly toasts = signal<Toast[]>([]);

  private suivant = 1;
  private parDefautEnAttente: ReturnType<typeof setTimeout> | null = null;

  succes(titre: string, detail?: string): void {
    this.afficher('succes', titre, detail);
  }

  info(titre: string, detail?: string): void {
    this.afficher('info', titre, detail);
  }

  erreur(titre: string, detail?: string): void {
    this.afficher('erreur', titre, detail);
  }

  /**
   * Message générique de l'intercepteur. Il attend la fin du tour en cours :
   * le retour de l'écran s'exécute juste après la réponse, et s'il affiche
   * son propre message, celui-ci est annulé.
   */
  parDefaut(titre: string): void {
    this.annulerParDefaut();
    this.parDefautEnAttente = setTimeout(() => {
      this.parDefautEnAttente = null;
      this.ajouter('succes', titre);
    }, 0);
  }

  fermer(id: number): void {
    this.toasts.update((liste) => liste.filter((t) => t.id !== id));
  }

  private afficher(genre: GenreToast, titre: string, detail?: string): void {
    this.annulerParDefaut();
    this.ajouter(genre, titre, detail);
  }

  private ajouter(genre: GenreToast, titre: string, detail?: string): void {
    const toast: Toast = { id: this.suivant++, genre, titre, detail, duree: DUREES[genre] };
    this.toasts.update((liste) => [...liste, toast].slice(-MAXIMUM));
    setTimeout(() => this.fermer(toast.id), toast.duree);
  }

  private annulerParDefaut(): void {
    if (this.parDefautEnAttente !== null) {
      clearTimeout(this.parDefautEnAttente);
      this.parDefautEnAttente = null;
    }
  }
}
