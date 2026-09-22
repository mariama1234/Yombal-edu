import { Component, OnDestroy, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { environment } from '../../../environments/environment';
import { Classe, DossierEleve, Paginated, PeriodeScolaire } from '../../core/models/models';
import { AnneeScolaireService } from '../../core/services/annee-scolaire.service';
import { AuthService } from '../../core/services/auth.service';
import { CreerAccesComponent } from '../../shared/acces/creer-acces.component';
import { ToastService } from '../../core/services/toast.service';
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
  imports: [CommonModule, ReactiveFormsModule, RouterLink, EleveFinancesComponent, CreerAccesComponent],
  templateUrl: './eleve-dossier.component.html',
})
export class EleveDossierComponent implements OnInit, OnDestroy {
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

  private auth = inject(AuthService);
  private toast = inject(ToastService);
  /** Seul un administrateur ajoute, change ou retire la photo. */
  // Ce que le profil peut faire depuis le dossier (matrice des autorisations).
  peutModifierEleve = computed(() => this.auth.peutEcrire('eleves'));
  peutInscrire = computed(() => this.auth.peutEcrire('inscriptions'));
  voitCaisse = computed(() => this.auth.peutLire('journal_caisse'));
  voitNotes = computed(() => this.auth.peutLire('notes'));
  /** Créer le compte de connexion de l'élève : gestion des comptes, donc direction. */
  peutCreerLAcces = computed(() => this.auth.peutEcrire('comptes'));

  /** Fenêtre « Créer son accès » ouverte. */
  accesOuvert = signal(false);
  /** Photo lue en binaire, affichée par une adresse locale au navigateur. */
  photo = signal<string | null>(null);
  photoEnCours = signal(false);
  /** Un fichier est glissé au-dessus de la zone photo. */
  survol = signal(false);

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

  /** Formulaire de l'autorisation exceptionnelle d'entrer en classe, ouvert ou non. */
  autorisationOuverte = signal(false);
  readonly aideDroits = "Les droits d'inscription doivent d'abord être réglés.";

  formAutorisation = this.fb.group({
    motif: ['', [Validators.required, Validators.minLength(3)]],
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

  charger(): void {
    this.chargement.set(true);
    this.scolarite.dossier(this.idEleve).subscribe({
      next: (d) => {
        this.dossier.set(d);
        this.chargement.set(false);
        this.chargerPhoto(d.eleve.a_photo);
      },
      error: () => {
        this.erreur.set("Ce dossier est introuvable.");
        this.chargement.set(false);
      },
    });
  }

  private chargerPhoto(aPhoto: boolean): void {
    this.libererPhoto();
    if (!aPhoto) return;
    this.scolarite.photo(this.idEleve).subscribe({
      next: (image) => this.photo.set(URL.createObjectURL(image)),
      error: () => this.photo.set(null),
    });
  }

  private libererPhoto(): void {
    const adresse = this.photo();
    if (adresse) URL.revokeObjectURL(adresse);
    this.photo.set(null);
  }

  choisirPhoto(evenement: Event): void {
    const champ = evenement.target as HTMLInputElement;
    const fichier = champ.files?.[0];
    // Vidé tout de suite : renvoyer le même fichier après une erreur doit rester possible.
    champ.value = '';
    if (fichier) this.envoyerPhoto(fichier);
  }

  /** Glisser-déposer d'une image sur la zone photo. */
  deposerPhoto(evenement: DragEvent): void {
    evenement.preventDefault();
    this.survol.set(false);
    const fichier = evenement.dataTransfer?.files?.[0];
    if (!fichier) return;
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(fichier.type)) {
      this.erreur.set('Format non reconnu : déposez une photo JPEG, PNG ou WebP.');
      return;
    }
    this.envoyerPhoto(fichier);
  }

  private envoyerPhoto(fichier: File): void {
    if (fichier.size > 3 * 1024 * 1024) {
      this.erreur.set('Photo trop volumineuse (maximum 3 Mo).');
      return;
    }
    this.photoEnCours.set(true);
    this.erreur.set(null);
    this.message.set(null);
    this.scolarite.envoyerPhoto(this.idEleve, fichier).subscribe({
      next: () => {
        this.photoEnCours.set(false);
        this.toast.succes('Photo enregistrée');
        this.chargerPhoto(true);
      },
      error: (e) => {
        this.photoEnCours.set(false);
        this.erreur.set(this.messageErreur(e));
      },
    });
  }

  retirerPhoto(): void {
    if (!confirm('Retirer la photo de ce dossier ?')) return;
    this.photoEnCours.set(true);
    this.erreur.set(null);
    this.scolarite.supprimerPhoto(this.idEleve).subscribe({
      next: () => {
        this.photoEnCours.set(false);
        this.libererPhoto();
        this.toast.succes('Photo retirée');
      },
      error: (e) => {
        this.photoEnCours.set(false);
        this.erreur.set(this.messageErreur(e));
      },
    });
  }

  ngOnDestroy(): void {
    this.libererPhoto();
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
        this.toast.succes(succes);
        this.mouvement.set(null);
        this.occupe.set(false);
        this.formClasse.reset();
        this.formRadiation.reset();
        this.formTransfert.reset();
        this.autorisationOuverte.set(false);
        this.formAutorisation.reset();
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

  /** Exception : l'élève suit les cours, son inscription reste incomplète. */
  autoriserEnClasse(): void {
    if (this.formAutorisation.invalid) return this.formAutorisation.markAllAsTouched();
    const eleve = this.dossier()?.eleve;
    const motif = this.formAutorisation.getRawValue().motif ?? '';
    this.appliquer(
      () => this.scolarite.autoriserEnClasse(this.idEleve, { motif }),
      `${eleve?.prenom ?? ''} ${eleve?.nom ?? ''} est autorisé(e) à entrer en classe`.trim(),
    );
  }

  retirerAutorisation(): void {
    if (!confirm("Retirer l'autorisation d'entrer en classe ? L'élève sortira de la liste de sa classe.")) return;
    this.appliquer(() => this.scolarite.retirerAutorisation(this.idEleve), 'Autorisation retirée');
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
