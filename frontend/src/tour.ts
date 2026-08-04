const TOUR_SEEN_KEY = 'aop_tour_seen'

/** Un seul appareil/navigateur ne suffit pas à identifier une personne (contrairement au
 * fait d'avoir sa propre clé API) — stocker « vu » par navigateur plutôt que côté serveur est un
 * compromis volontaire pour ce tutoriel à faible enjeu : au pire, il se réaffiche une fois sur un
 * nouvel appareil, ce qui n'est jamais problématique. */
export function hasSeenTour(): boolean {
  try {
    return localStorage.getItem(TOUR_SEEN_KEY) === '1'
  } catch {
    return true // stockage indisponible (navigation privée…) : ne jamais bloquer sur cet état
  }
}

export function markTourSeen(): void {
  try {
    localStorage.setItem(TOUR_SEEN_KEY, '1')
  } catch {
    // ignore
  }
}
