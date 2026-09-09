import { Component, Input, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { RouterLink } from '@angular/router';
import {
  LigneFrais,
  MethodePaiement,
  SituationFinanciereEleve,
} from '../../core/models/models';
import { FinanceService } from '../finances/finance.service';

/**
 * Onglet « Finances » du dossier de l'élève.
 *
 * Tout ce qu'un administrateur doit comprendre sans ouvrir une autre page :
 * ce qui était dû, ce qui a été payé, ce qui reste, quels mois sont soldés,
 * et par quel moyen chaque règlement est arrivé.
 *
 * Deux partis pris :
 *
 * * **Une année à la fois.** Le sélecteur en tête n'est pas un confort : les
 *   frais d'une année n'ont jamais été facturés avec ceux d'une autre, et un
 *   total qui les additionne ne correspond à aucune facture réelle. Un ancien
 *   élève garde ainsi une ardoise lisible année par année.
 * * **L'écran ne recalcule rien.** Résumé, échéancier et statuts viennent du
 *   serveur, qui les tire des paiements. Un montant recalculé côté navigateur
 *   finirait par diverger de celui du reçu et de celui de la caisse.
 */
@Component({
  selector: 'app-eleve-finances',
  standalone: true,
  imports: [CommonModule, ReactiveFormsModule, RouterLink],
  templateUrl: './eleve-finances.component.html',
})
export class EleveFinancesComponent implements OnInit {
  @Input({ required: true }) idEleve!: number;

  private fb = inject(FormBuilder);
  private finance = inject(FinanceService);

  situation = signal<SituationFinanciereEleve | null>(null);
  chargement = signal(true);
  erreur = signal<string | null>(null);
  message = signal<string | null>(null);
  occupe = signal(false);
  telechargement = signal<number | null>(null);

  /** Formulaire d'encaissement ouvert, et frais en cours de remise. */
  saisieOuverte = signal(false);
  remiseOuverte = signal<number | null>(null);

  methodes: { value: MethodePaiement; label: string }[] = [
    { value: 'especes', label: 'Espèces' },
    { value: 'orange_money', label: 'Orange Money' },
    { value: 'wave', label: 'Wave' },
    { value: 'free_money', label: 'Free Money' },
    { value: 'carte_bancaire', label: 'Carte bancaire' },
  ];

  formPaiement = this.fb.group({
    montant: [null as number | null, [Validators.required, Validators.min(1)]],
    methode: ['especes' as MethodePaiement, Validators.required],
    mois: [''],
    date_paiement: [''],
    reference_transaction: [''],
    commentaire: [''],
  });

  formRemise = this.fb.group({
    remise: [null as number | null, [Validators.required, Validators.min(0)]],
    motif_remise: [''],
  });

  /** Reste à payer de l'année affichée : le plafond d'un encaissement. */
  reste = computed(() => this.situation()?.resume.reste ?? 0);

  /**
   * Aucun frais n'a encore été émis pour cette année.
   *
   * C'est l'état d'un pré-inscrit qu'on vient de créer, et il piégeait
   * l'écran : sans frais, le reste à payer vaut zéro, le bouton d'
   * encaissement se désactivait, et l'élève ne pouvait donc jamais payer —
   * donc jamais voir son inscription confirmée. Il faut distinguer
   * « tout est soldé » de « il n'y a rien à solder ».
   */
  sansFrais = computed(() => (this.situation()?.resume.nombre_frais ?? 0) === 0);

  /**
   * Le bouton n'est éteint que lorsqu'il n'y a réellement rien à encaisser :
   * des frais existent et ils sont tous réglés.
   */
  peutEncaisser = computed(() => {
    const situation = this.situation();
    if (!situation?.annee) return false;
    return this.sansFrais() || this.reste() > 0;
  });

  /**
   * Mois encore ouverts, proposés à la saisie. Un règlement vise presque
   * toujours une échéance précise — « la mensualité d'octobre » — et laisser
   * choisir évite d'imputer au mauvais mois.
   */
  moisOuverts = computed(
    () => this.situation()?.echeancier.filter((m) => m.mois && m.reste > 0) ?? [],
  );

  ngOnInit(): void {
    this.charger();
  }

  charger(idAnnee?: number | null): void {
    this.chargement.set(true);
    this.erreur.set(null);
    this.finance.situationEleve(this.idEleve, idAnnee ?? null).subscribe({
      next: (s) => {
        this.situation.set(s);
        this.chargement.set(false);
      },
      error: (e) => {
        this.erreur.set(this.messageErreur(e));
        this.chargement.set(false);
      },
    });
  }

  changerAnnee(valeur: string): void {
    this.fermer();
    this.charger(Number(valeur));
  }

  ouvrirSaisie(): void {
    // Sans frais émis, il n'y a rien sur quoi imputer : ouvrir le formulaire
    // ne mènerait qu'à un refus du serveur. On montre la marche à suivre.
    this.remiseOuverte.set(null);
    this.saisieOuverte.update((v) => !v);
    this.message.set(null);
    this.erreur.set(null);
    this.formPaiement.reset({ methode: 'especes', mois: '', date_paiement: '',
                              reference_transaction: '', commentaire: '' });
  }

  ouvrirRemise(frais: LigneFrais): void {
    this.saisieOuverte.set(false);
    this.remiseOuverte.update((id) => (id === frais.id ? null : frais.id));
    this.formRemise.reset({ remise: frais.remise, motif_remise: frais.motif_remise ?? '' });
  }

  fermer(): void {
    this.saisieOuverte.set(false);
    this.remiseOuverte.set(null);
  }

  encaisser(): void {
    if (this.formPaiement.invalid) return this.formPaiement.markAllAsTouched();
    const v = this.formPaiement.getRawValue();
    this.occupe.set(true);
    this.erreur.set(null);

    this.finance
      .encaisserPourEleve({
        id_eleve: this.idEleve,
        id_annee_scolaire: this.situation()?.annee?.id ?? null,
        montant: v.montant,
        methode: v.methode,
        mois: v.mois || null,
        // Le champ HTML rend « 2026-10-05 » ; l'API attend un instant.
        date_paiement: v.date_paiement ? `${v.date_paiement}T12:00:00` : null,
        reference_transaction: v.reference_transaction || null,
        commentaire: v.commentaire || null,
      })
      .subscribe({
        next: (resultat) => {
          // Le serveur renvoie la situation déjà recalculée : pas de second
          // appel, et surtout aucun risque d'afficher un état intermédiaire.
          this.situation.set(resultat.situation);
          this.message.set(
            `Paiement enregistré (${resultat.paiements.length} échéance(s) mouvementée(s)).`,
          );
          this.saisieOuverte.set(false);
          this.occupe.set(false);
        },
        error: (e) => {
          this.erreur.set(this.messageErreur(e));
          this.occupe.set(false);
        },
      });
  }

  appliquerRemise(idFrais: number): void {
    if (this.formRemise.invalid) return this.formRemise.markAllAsTouched();
    const v = this.formRemise.getRawValue();
    this.occupe.set(true);
    this.erreur.set(null);

    this.finance
      .modifierFrais(idFrais, {
        remise: v.remise,
        motif_remise: v.motif_remise || null,
      })
      .subscribe({
        next: () => {
          this.message.set('Remise enregistrée.');
          this.remiseOuverte.set(null);
          this.occupe.set(false);
          this.charger(this.situation()?.annee?.id ?? null);
        },
        error: (e) => {
          this.erreur.set(this.messageErreur(e));
          this.occupe.set(false);
        },
      });
  }

  /** Le reçu est produit par le serveur : même document que celui archivé. */
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

  private messageErreur(erreur: unknown): string {
    const corps = (erreur as { error?: Record<string, unknown> })?.error;
    if (!corps) return "L'opération a échoué.";
    const premiere = Object.values(corps)[0];
    return Array.isArray(premiere) ? String(premiere[0]) : String(premiere);
  }
}
