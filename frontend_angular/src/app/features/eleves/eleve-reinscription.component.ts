import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { AnneeScolaireService } from '../../core/services/annee-scolaire.service';
import { ContexteReinscription } from '../../core/models/models';
import { ChoixFraisComponent, FraisAffiche } from '../../shared/frais/choix-frais.component';
import { EleveService } from './eleve.service';
import { ToastService } from '../../core/services/toast.service';

/**
 * Réinscription d'un élève, en une page pré-remplie.
 *
 * L'écran ne pose que deux questions — dans quelle classe il passe, et quels
 * frais au choix il garde. L'élève, sa provenance et l'année visée viennent du
 * serveur : les faire ressaisir n'apporterait rien et ouvrirait la porte au
 * couple incohérent (l'élève de 4ème A réinscrit « depuis » la 6ème B).
 *
 * Les frais se présentent comme à l'inscription, par le même bloc : montants
 * du niveau de la classe d'arrivée, frais dus d'office en tête, frais au choix
 * de l'an passé déjà cochés.
 *
 * Il n'y a volontairement pas de suggestion automatique de la classe
 * supérieure : les niveaux n'ont pas d'ordre en base, et deviner « après la
 * 4ème vient la 3ème » à partir du libellé se tromperait à la première école
 * qui nomme ses niveaux autrement.
 */
@Component({
  selector: 'app-eleve-reinscription',
  standalone: true,
  imports: [CommonModule, ReactiveFormsModule, RouterLink, ChoixFraisComponent],
  templateUrl: './eleve-reinscription.component.html',
})
export class EleveReinscriptionComponent implements OnInit {
  private fb = inject(FormBuilder);
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  private eleves = inject(EleveService);
  private toast = inject(ToastService);
  annees = inject(AnneeScolaireService);

  idEleve = 0;
  contexte = signal<ContexteReinscription | null>(null);
  chargement = signal(true);
  occupe = signal(false);
  erreur = signal<string | null>(null);
  resultat = signal<string | null>(null);
  /** Frais au choix retenus pour la nouvelle année. */
  fraisCoches = signal<number[]>([]);

  private idClasse = signal<number | null>(null);

  form = this.fb.group({
    id_classe: [null as number | null, Validators.required],
    date_inscription: [''],
  });

  /** Classes de l'année cible, celle de l'an passé exclue de fait. */
  classes = computed(() => this.contexte()?.classes ?? []);

  classeChoisie = computed(() => this.classes().find((c) => c.id === this.idClasse()) ?? null);

  /** Niveau de la classe d'arrivée : il fixe le montant de chaque frais. */
  niveauCible = computed(() => this.classeChoisie()?.id_niveau ?? null);

  /** Ce que le bloc « Frais » affiche en titre ; `null` tant que la classe n'est pas choisie. */
  libelleCible = computed(() => {
    const classe = this.classeChoisie();
    return classe ? classe.niveau ?? classe.nom : null;
  });

  /** Les frais de l'école, au montant du niveau de la classe d'arrivée. */
  fraisAffiches = computed<FraisAffiche[]>(() => {
    const niveau = this.niveauCible();
    return (this.contexte()?.types_frais ?? []).map((t) => {
      const tarif = niveau !== null ? t.tarifs?.[String(niveau)] : undefined;
      return {
        id: t.id,
        libelle: t.libelle_affiche,
        nature: t.nature,
        montant: Number(tarif ?? t.montant_defaut),
        tarifNiveau: tarif !== undefined && tarif !== null,
        mensuel: t.mensuel,
        reconduit: t.reconduit,
      };
    });
  });

  ngOnInit(): void {
    this.idEleve = Number(this.route.snapshot.paramMap.get('id'));
    this.charger();
    this.form.controls.id_classe.valueChanges.subscribe((v) => this.idClasse.set(v));
  }

  private charger(): void {
    this.chargement.set(true);
    const annee = this.annees.idAnnee();
    const params: Record<string, string> = {};
    if (annee !== null) params['id_annee_scolaire'] = String(annee);

    this.eleves.contexteReinscription(this.idEleve, params).subscribe({
      next: (c) => {
        this.contexte.set(c);
        this.form.patchValue({ date_inscription: c.annee_cible.date_debut });
        // Ce que la famille avait l'an passé arrive coché : on décoche ce qui change.
        this.fraisCoches.set(c.types_frais.filter((t) => t.reconduit).map((t) => t.id));
        this.chargement.set(false);
      },
      error: (e) => {
        this.erreur.set(this.messageErreur(e));
        this.chargement.set(false);
      },
    });
  }

  valider(): void {
    if (this.form.invalid) return this.form.markAllAsTouched();
    const v = this.form.getRawValue();
    this.occupe.set(true);
    this.erreur.set(null);

    this.eleves
      .reinscrire(this.idEleve, {
        id_classe: v.id_classe,
        date_inscription: v.date_inscription || null,
        frais_optionnels: this.fraisCoches(),
      })
      .subscribe({
        next: (r) => {
          this.resultat.set(
            `${r.eleve} passe de ${r.classe_precedente} (${r.annee_precedente}) `
            + `à ${r.classe} pour ${r.annee}.`
            + (r.frais_emis ? ` ${r.frais_emis} ligne(s) de frais émise(s).` : '')
            + (r.avertissement ? ` ${r.avertissement}` : ''),
          );
          this.toast.succes(`Réinscription enregistrée : ${r.eleve}`, `${r.classe} — ${r.annee}`);
          this.occupe.set(false);
        },
        error: (e) => {
          this.erreur.set(this.messageErreur(e));
          this.occupe.set(false);
        },
      });
  }

  basculerFrais(id: number): void {
    this.fraisCoches.update((ids) => (ids.includes(id) ? ids.filter((i) => i !== id) : [...ids, id]));
  }

  /** Enchaîner : la file d'attente est le lieu naturel où revenir. */
  retourFile(): void {
    this.router.navigate(['/eleves'], { queryParams: { vue: 'a_reinscrire' } });
  }

  private messageErreur(erreur: unknown): string {
    const corps = (erreur as { error?: Record<string, unknown> })?.error;
    if (!corps) return "La réinscription a échoué.";
    const premiere = Object.values(corps)[0];
    return Array.isArray(premiere) ? String(premiere[0]) : String(premiere);
  }
}
