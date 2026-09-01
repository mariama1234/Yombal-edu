import { Component, OnInit, signal, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { environment } from '../../../environments/environment';
import { FraisEleve, Paiement } from '../../core/models/models';

@Component({
  selector: 'app-paiement-form',
  standalone: true,
  imports: [CommonModule, ReactiveFormsModule, RouterLink],
  templateUrl: './paiement-form.component.html',
})
export class PaiementFormComponent implements OnInit {
  private fb = inject(FormBuilder);
  window = window;
  frais = signal<FraisEleve | null>(null);
  recuGenere = signal<Paiement | null>(null);
  enregistrement = signal(false);

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
    reference_transaction: [''],
  });

  constructor(
    private route: ActivatedRoute,
    private router: Router,
    private http: HttpClient
  ) {}

  ngOnInit(): void {
    const idFrais = this.route.snapshot.paramMap.get('idFrais');
    this.http.get<FraisEleve>(`${environment.apiUrl}/frais-eleves/${idFrais}/`).subscribe((f) => {
      this.frais.set(f);
      this.form.patchValue({ montant: f.solde_restant ?? f.montant_du });
    });
  }

  submit(): void {
    if (this.form.invalid || !this.frais()) return;
    this.enregistrement.set(true);

    this.http
      .post<Paiement>(`${environment.apiUrl}/paiements/`, {
        id_frais_eleve: this.frais()!.id,
        ...this.form.getRawValue(),
      })
      .subscribe({
        next: (paiement) => {
          this.enregistrement.set(false);
          this.recuGenere.set(paiement);
        },
        error: () => this.enregistrement.set(false),
      });
  }
}
