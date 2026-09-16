import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { FraisEleve, Paiement } from '../../core/models/models';
import { FinanceService } from './finance.service';
import { ToastService } from '../../core/services/toast.service';

@Component({
  selector: 'app-paiement-form',
  standalone: true,
  imports: [CommonModule, ReactiveFormsModule, RouterLink],
  templateUrl: './paiement-form.component.html',
})
export class PaiementFormComponent implements OnInit {
  private fb = inject(FormBuilder);
  private route = inject(ActivatedRoute);
  private finance = inject(FinanceService);
  private toast = inject(ToastService);

  frais = signal<FraisEleve | null>(null);
  paiement = signal<Paiement | null>(null);
  erreur = signal<string | null>(null);
  enregistrement = signal(false);
  telechargement = signal(false);

  methodes = [
    { value: 'orange_money', label: 'Orange Money' },
    { value: 'wave', label: 'Wave' },
    { value: 'free_money', label: 'Free Money' },
    { value: 'carte_bancaire', label: 'Carte bancaire' },
    { value: 'especes', label: 'Espèces' },
  ];

  form = this.fb.group({
    montant: [0, [Validators.required, Validators.min(1)]],
    methode: ['especes', Validators.required],
    date_paiement: [''],
    reference_transaction: [''],
    commentaire: [''],
  });

  ngOnInit(): void {
    const idFrais = this.route.snapshot.paramMap.get('idFrais')!;
    this.finance.unFrais(idFrais).subscribe({
      next: (f) => {
        this.frais.set(f);
        this.form.patchValue({ montant: Number(f.solde_restant ?? f.montant_du) });
      },
      error: () => this.erreur.set('Frais introuvable.'),
    });
  }

  submit(): void {
    if (this.form.invalid || !this.frais()) return;
    this.enregistrement.set(true);
    this.erreur.set(null);

    const v = this.form.getRawValue();
    this.finance
      .encaisser({
        id_frais_eleve: this.frais()!.id,
        montant: v.montant,
        methode: v.methode,
        // Le champ HTML rend « 2026-10-05 » ; l'API attend un instant. Omis,
        // le serveur date le paiement de maintenant.
        date_paiement: v.date_paiement ? `${v.date_paiement}T12:00:00` : undefined,
        reference_transaction: v.reference_transaction || undefined,
        commentaire: v.commentaire || undefined,
      })
      .subscribe({
        next: (paiement) => {
          this.paiement.set(paiement);
          this.toast.succes('Paiement enregistré',
                            paiement.numero_recu ? `Reçu n° ${paiement.numero_recu}` : undefined);
          this.enregistrement.set(false);
        },
        error: (e) => {
          this.erreur.set(this.message(e));
          this.enregistrement.set(false);
        },
      });
  }

  /** Le reçu est produit par le serveur : même document que celui archivé. */
  telechargerRecu(): void {
    const id = this.paiement()?.id_recu;
    if (!id) return;
    this.telechargement.set(true);
    this.finance.telecharger(`/recus/${id}/pdf/`).subscribe({
      next: () => this.telechargement.set(false),
      error: () => {
        this.erreur.set('Le reçu n’a pas pu être téléchargé.');
        this.telechargement.set(false);
      },
    });
  }

  private message(erreur: unknown): string {
    const corps = (erreur as { error?: Record<string, unknown> })?.error;
    if (!corps) return "L'encaissement a échoué.";
    const premiere = Object.values(corps)[0];
    return Array.isArray(premiere) ? String(premiere[0]) : String(premiere);
  }
}
