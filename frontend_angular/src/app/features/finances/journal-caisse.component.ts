import { Component, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { environment } from '../../../environments/environment';
import { JournalCaisse, Paginated } from '../../core/models/models';

@Component({
  selector: 'app-journal-caisse',
  standalone: true,
  imports: [CommonModule, RouterLink],
  templateUrl: './journal-caisse.component.html',
})
export class JournalCaisseComponent implements OnInit {
  mouvements = signal<JournalCaisse[]>([]);
  loading = signal(true);

  constructor(private http: HttpClient) {}

  ngOnInit(): void {
    this.http.get<Paginated<JournalCaisse>>(`${environment.apiUrl}/journal-caisse/`).subscribe({
      next: (res) => {
        this.mouvements.set(res.results);
        this.loading.set(false);
      },
      error: () => this.loading.set(false),
    });
  }
}
