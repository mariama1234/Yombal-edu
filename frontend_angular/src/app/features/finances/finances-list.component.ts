import { Component, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { environment } from '../../../environments/environment';
import { FraisEleve, Paginated } from '../../core/models/models';

@Component({
  selector: 'app-finances-list',
  standalone: true,
  imports: [CommonModule, RouterLink],
  templateUrl: './finances-list.component.html',
})
export class FinancesListComponent implements OnInit {
  frais = signal<FraisEleve[]>([]);
  loading = signal(true);

  constructor(private http: HttpClient) {}

  ngOnInit(): void {
    this.charger();
  }

  charger(): void {
    this.loading.set(true);
    this.http.get<Paginated<FraisEleve>>(`${environment.apiUrl}/frais-eleves/impayes/`).subscribe({
      next: (res) => {
        this.frais.set(res.results ?? (res as any));
        this.loading.set(false);
      },
      error: () => this.loading.set(false),
    });
  }
}
