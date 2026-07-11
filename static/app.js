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

function selectKayak(card, options = {}) {
  const changed = selectedKayakId !== card.dataset.kayakId;
  selectedKayakId = card.dataset.kayakId;
  kayakInput.value = selectedKayakId;
  selectedKayakName.textContent = card.dataset.kayakName;
  selectedKayakPrice.textContent = `Rs ${card.dataset.kayakPrice}/day`;
  if (changed && !options.keepDate) {
    bookingDateInput.value = "";
  }

  kayakCards.forEach((kayakCard) => {
    const isSelected = kayakCard === card;
    kayakCard.classList.toggle("selected", isSelected);
    kayakCard.setAttribute("aria-pressed", String(isSelected));
  });

  loadAvailability();
  if (options.scroll !== false) {
    document.querySelector("#availability")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
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

// Inline validation: message appears when you leave a field, clears as you fix it.
const fieldValidators = {
  customer_name: (value) => (value.trim() ? "" : "Please enter your full name."),
  customer_email: (value) => (/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value.trim()) ? "" : "Please enter a valid email address."),
  booking_date: (value) => (value ? "" : "Please pick a booking date."),
};

function showFieldError(input, message) {
  let error = input.parentElement.querySelector(".field-error");
  if (!message) {
    input.removeAttribute("aria-invalid");
    error?.remove();
    return;
  }
  if (!error) {
    error = document.createElement("span");
    error.className = "field-error";
    error.id = `error-${input.name.replace("_", "-")}`;
    input.parentElement.appendChild(error);
  }
  error.textContent = message;
  input.setAttribute("aria-invalid", "true");
  input.setAttribute("aria-describedby", error.id);
}

document.querySelectorAll(".booking-form [name]").forEach((input) => {
  const validate = fieldValidators[input.name];
  if (!validate) {
    return;
  }
  input.addEventListener("blur", () => showFieldError(input, validate(input.value)));
  input.addEventListener("input", () => {
    if (input.getAttribute("aria-invalid")) {
      showFieldError(input, validate(input.value));
    }
  });
});

window.addEventListener("load", () => {
  // After a server-side validation error, restore the selected kayak and
  // bring the visitor back to the form instead of dumping them at the top.
  const serverError = document.querySelector(".field-error");
  const initialCard = [...kayakCards].find((card) => card.dataset.kayakId === kayakInput?.value);
  if (initialCard && !initialCard.classList.contains("selected")) {
    selectKayak(initialCard, { scroll: false, keepDate: true });
  }
  if (serverError) {
    document.querySelector("#booking")?.scrollIntoView({ block: "start" });
  }
  loadAvailability();
});

function dismissToast(toast) {
  toast.classList.add("is-leaving");
  setTimeout(() => toast.remove(), 350);
}

document.querySelectorAll(".toast").forEach((toast) => {
  toast.querySelector("[data-toast-close]")?.addEventListener("click", () => dismissToast(toast));
  if (!toast.hasAttribute("data-persistent")) {
    setTimeout(() => dismissToast(toast), 6000);
  }
});
