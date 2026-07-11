const calendarGrid = document.querySelector("#calendarGrid");
const calendarRange = document.querySelector("#calendarRange");
const previousDates = document.querySelector("#previousDates");
const nextDates = document.querySelector("#nextDates");
const bookingDateInput = document.querySelector("#bookingDate");
const kayakInput = document.querySelector("[name='kayak_id']");
const selectedKayakName = document.querySelector("#selectedKayakName");
const selectedKayakPrice = document.querySelector("#selectedKayakPrice");
const selectedKayakShortcut = document.querySelector("#selectedKayakShortcut");
const kayakCards = document.querySelectorAll(".kayak-card");
const alertPopup = document.querySelector("#alertPopup");
const alertPopupClose = document.querySelector("[data-popup-close]");

let calendarStart = new Date();
let selectedKayakId = kayakInput?.value || "1";

function isoDate(date) {
  return date.toISOString().slice(0, 10);
}

function shiftDays(date, days) {
  const copy = new Date(date);
  copy.setDate(copy.getDate() + days);
  return copy;
}

async function loadAvailability() {
  if (!calendarGrid) {
    return;
  }
  const response = await fetch(`/api/availability?kayak_id=${selectedKayakId}&start=${isoDate(calendarStart)}&days=14`);
  const days = await response.json();
  calendarGrid.innerHTML = "";

  if (days.length) {
    calendarRange.textContent = `${days[0].label} - ${days[days.length - 1].label}`;
  }

  days.forEach((day) => {
    const tile = document.createElement("button");
    tile.type = "button";
    tile.className = `date-tile ${day.available ? "available" : "booked"}`;
    tile.disabled = !day.available;
    tile.innerHTML = `
      <span>${day.weekday}</span>
      <strong>${day.label}</strong>
      <em class="status-pill">${day.available ? "Available" : "Booked"}</em>
    `;
    tile.addEventListener("click", () => {
      bookingDateInput.value = day.date;
      bookingDateInput.focus();
      document.querySelector("#booking")?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    calendarGrid.appendChild(tile);
  });
}

function selectKayak(card) {
  selectedKayakId = card.dataset.kayakId;
  kayakInput.value = selectedKayakId;
  selectedKayakName.textContent = card.dataset.kayakName;
  selectedKayakPrice.textContent = `Rs ${card.dataset.kayakPrice}/day`;
  bookingDateInput.value = "";

  kayakCards.forEach((kayakCard) => {
    const isSelected = kayakCard === card;
    kayakCard.classList.toggle("selected", isSelected);
    kayakCard.setAttribute("aria-pressed", String(isSelected));
  });

  loadAvailability();
  document.querySelector("#availability")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

previousDates?.addEventListener("click", () => {
  const today = new Date();
  const previous = shiftDays(calendarStart, -14);
  calendarStart = previous < today ? today : previous;
  loadAvailability();
});

nextDates?.addEventListener("click", () => {
  calendarStart = shiftDays(calendarStart, 14);
  loadAvailability();
});

kayakCards.forEach((card) => {
  card.addEventListener("click", () => selectKayak(card));
});

selectedKayakShortcut?.addEventListener("click", () => {
  document.querySelector("#fleet")?.scrollIntoView({ behavior: "smooth", block: "start" });
});

window.addEventListener("load", () => {
  if (window.lucide) {
    window.lucide.createIcons();
  }
  loadAvailability();
});

function closeAlertPopup() {
  alertPopup?.classList.remove("is-open");
}

alertPopupClose?.addEventListener("click", closeAlertPopup);
alertPopup?.addEventListener("click", (event) => {
  if (event.target === alertPopup) {
    closeAlertPopup();
  }
});
window.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeAlertPopup();
  }
});
