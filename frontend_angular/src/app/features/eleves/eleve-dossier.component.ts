import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { environment } from '../../../environments/environment';
import { Classe, DossierEleve, Paginated, PeriodeScolaire } from '../../core/models/models';
import { AnneeScolaireService } from '../../core/services/annee-scolaire.service';
import { EleveFinancesComponent } from './eleve-finances.component';
import { ScolariteService } from './scolarite.service';

/** Les trois mouvements possibles, ou aucun formulaire ouvert. */
type Mouvement = 'classe' | 'radiation' | 'transfert' | null;

/** Les deux faces du dossier : la scolarité, et l'argent. */
type Onglet = 'scolarite' | 'finances';

/**
 * Dossier numérique de l'élève (1.2 et 1.3 de la priorisation).
 *
 * Tout ce qui concerne un élève tient sur cet écran : identité, parents,
 * parcours, pièces, résultats, situation financière et assiduité. Le serveur
 * assemble le tout en une réponse — le front n'a pas à recoudre six appels.
 *
 * Les mouvements sont volontairement derrière un formulaire à ouvrir : radier
 * un élève d'un seul clic depuis une liste est trop facile à faire par erreur.
 */
@Component({
  selector: 'app-eleve-dossier',
  standalone: true,
  imports: [CommonModule, ReactiveFormsModule, RouterLink, EleveFinancesComponent],
  templateUrl: './eleve-dossier.component.html',
})
export class EleveDossierComponent implements OnInit {
  private route = inject(ActivatedRoute);
  private http = inject(HttpClient);
  private fb = inject(FormBuilder);
  private scolarite = inject(ScolariteService);
  annees = inject(AnneeScolaireService);

  idEleve = 0;
  dossier = signal<DossierEleve | null>(null);
  classes = signal<Classe[]>([]);
  periodes = signal<PeriodeScolaire[]>([]);

  /**
   * Le financier est derrière son propre onglet plutôt qu'à la suite : le
   * dossier tient déjà six blocs, et l'administrateur qui cherche « où en est
   * le paiement » ne doit pas faire défiler les bulletins pour y arriver.
   */
  onglet = signal<Onglet>('scolarite');

  mouvement = signal<Mouvement>(null);
  erreur = signal<string | null>(null);
  message = signal<string | null>(null);
  occupe = signal(false);
  chargement = signal(true);

  /**
   * L'élève a-t-il besoin d'être réinscrit pour l'année affichée ?
   *
   * Vrai quand il a un passé dans l'école mais aucune inscription sur cette
   * année-là. C'est la question qu'on se pose en ouvrant le dossier d'un
   * ancien à la rentrée, et jusqu'ici le dossier n'y répondait pas : il
   * affichait la classe de l'an dernier sans dire qu'elle était périmée.
   */
  aReinscrire = computed(() => {
    const dossier = this.dossier();
    const annee = this.annees.anneeCourante()?.libelle;
    if (!dossier || !annee || dossier.eleve.statut !== 'actif') return false;
    if (dossier.inscriptions.length === 0) return false;
    return !dossier.inscriptions.some((i) => i.annee === annee);
  });

  /** Un élève déjà sorti n'a plus de mouvement à subir. */
  sorti = computed(() => {
    const statut = this.dossier()?.eleve.statut;
    return statut === 'radie' || statut === 'transfere';
  });

  /** Classes de l'année en cours, hors classe actuelle de l'élève. */
  classesDisponibles = computed(() => {
    const courante = this.dossier()?.scolarite_courante;
    if (!courante) return [];
    return this.classes().filter(
      (c) => c.id_annee_scolaire === courante.id_annee_scolaire && c.id !== courante.id_classe,
    );
  });

  formClasse = this.fb.group({
    id_classe: [null as number | null, Validators.required],
    date_effet: [''],
    motif: [''],
  });

  formRadiation = this.fb.group({
    motif: ['', [Validators.required, Validators.minLength(3)]],
    date_effet: [''],
    commentaire: [''],
  });

  formTransfert = this.fb.group({
    etablissement: ['', Validators.required],
    date_effet: [''],
    commentaire: [''],
  });

  formReleve = this.fb.group({
    id_periode: [null as number | null],
  });

  ngOnInit(): void {
    this.idEleve = Number(this.route.snapshot.paramMap.get('id'));
    this.charger();

    this.http
      .get<Paginated<Classe>>(`${environment.apiUrl}/classes/`)
      .subscribe((r) => this.classes.set(r.results ?? []));
    this.http
      .get<Paginated<PeriodeScolaire>>(`${environment.apiUrl}/periodes/`)
      .subscribe((r) => {
        const periodes = r.results ?? [];
        this.periodes.set(periodes);
        if (periodes.length) this.formReleve.patchValue({ id_periode: periodes[0].id });
      });
  }

  private charger(): void {
    this.chargement.set(true);
    this.scolarite.dossier(this.idEleve).subscribe({
      next: (d) => {
        this.dossier.set(d);
        this.chargement.set(false);
      },
      error: () => {
        this.erreur.set("Ce dossier est introuvable.");
        this.chargement.set(false);
      },
    });
  }

  ouvrir(mouvement: Mouvement): void {
    this.mouvement.set(this.mouvement() === mouvement ? null : mouvement);
    this.erreur.set(null);
  }

  private appliquer(
    appel: () => ReturnType<ScolariteService['radier']>,
    succes: string,
  ): void {
    this.occupe.set(true);
    this.erreur.set(null);
    appel().subscribe({
      next: (d) => {
        this.dossier.set(d);
        this.message.set(succes);
        this.mouvement.set(null);
        this.occupe.set(false);
        this.formClasse.reset();
        this.formRadiation.reset();
        this.formTransfert.reset();
      },
      error: (e) => {
        this.erreur.set(this.messageErreur(e));
        this.occupe.set(false);
      },
    });
  }

  changerDeClasse(): void {
    if (this.formClasse.invalid) return this.formClasse.markAllAsTouched();
    const v = this.formClasse.getRawValue();
    this.appliquer(
      () => this.scolarite.changerDeClasse(this.idEleve, {
        id_classe: v.id_classe,
        date_effet: v.date_effet || null,
        motif: v.motif ?? '',
      }),
      'Changement de classe enregistré.',
    );
  }

  radier(): void {
    if (this.formRadiation.invalid) return this.formRadiation.markAllAsTouched();
    const v = this.formRadiation.getRawValue();
    this.appliquer(
      () => this.scolarite.radier(this.idEleve, {
        motif: v.motif,
        date_effet: v.date_effet || null,
        commentaire: v.commentaire ?? '',
      }),
      'Élève radié.',
    );
  }

  transferer(): void {
    if (this.formTransfert.invalid) return this.formTransfert.markAllAsTouched();
    const v = this.formTransfert.getRawValue();
    this.appliquer(
      () => this.scolarite.transferer(this.idEleve, {
        etablissement: v.etablissement,
        date_effet: v.date_effet || null,
        commentaire: v.commentaire ?? '',
      }),
      'Transfert enregistré.',
    );
  }

  telechargerReleve(): void {
    const periode = this.formReleve.getRawValue().id_periode;
    if (!periode) {
      this.erreur.set('Choisis une période avant de télécharger le relevé.');
      return;
    }
    this.erreur.set(null);
    this.scolarite
      .telecharger(`/eleves/${this.idEleve}/releve/export/`, { id_periode: periode })
      .subscribe({ error: (e) => this.erreur.set(this.messageErreur(e)) });
  }

  private messageErreur(erreur: unknown): string {
    const corps = (erreur as { error?: Record<string, unknown> })?.error;
    if (!corps) return "L'opération a échoué.";
    const premiere = Object.values(corps)[0];
    return Array.isArray(premiere) ? String(premiere[0]) : String(premiere);
  }
}
