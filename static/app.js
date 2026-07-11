const calendarGrid = document.querySelector("#calendarGrid");
const calendarRange = document.querySelector("#calendarRange");
const calendarSelection = document.querySelector("#calendarSelection");
const calendarKayakName = document.querySelector("#calendarKayakName");
const previousDates = document.querySelector("#previousDates");
const nextDates = document.querySelector("#nextDates");
const bookingDateInput = document.querySelector("#bookingDate");
const kayakInput = document.querySelector("[name='kayak_id']");
const selectedKayakName = document.querySelector("#selectedKayakName");
const selectedKayakPrice = document.querySelector("#selectedKayakPrice");
const selectedKayakShortcut = document.querySelector("#selectedKayakShortcut");
const kayakCards = document.querySelectorAll(".kayak-card");
const priceSummary = document.querySelector("#priceSummary");
const summaryLine = document.querySelector("#summaryLine");
const summaryTotal = document.querySelector("#summaryTotal");
const bookingBar = document.querySelector("#bookingBar");
const bookingBarPrice = document.querySelector("#bookingBarPrice");
const bookingBarMeta = document.querySelector("#bookingBarMeta");

const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

let selectedKayakId = kayakInput?.value || "1";
let selectedDate = bookingDateInput?.value || null;

const today = new Date();
today.setHours(0, 0, 0, 0);
let viewYear = today.getFullYear();
let viewMonth = today.getMonth();

// Local-date ISO string. Never toISOString(): that converts to UTC and can
// shift the date by a day depending on the visitor's timezone.
function isoDate(date) {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function currentKayakCard() {
  return [...kayakCards].find((card) => card.dataset.kayakId === selectedKayakId);
}

function prettyDate(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
}

function updateSelectionLine() {
  updateBookingSummary();
  if (!calendarSelection) {
    return;
  }
  if (!selectedDate) {
    calendarSelection.textContent = "No date selected yet.";
    return;
  }
  const card = currentKayakCard();
  const name = card ? card.dataset.kayakName : "Kayak";
  const price = card ? ` · Rs ${card.dataset.kayakPrice}` : "";
  calendarSelection.innerHTML = `<strong>${prettyDate(selectedDate)}</strong> · ${name}<strong>${price}</strong>`;
}

function updateBookingSummary() {
  const card = currentKayakCard();
  const rate = card ? Number(card.dataset.kayakPrice) : 0;
  if (bookingBarPrice) {
    bookingBarPrice.textContent = `Rs ${rate}`;
  }
  if (selectedDate) {
    if (priceSummary) {
      priceSummary.hidden = false;
      summaryLine.textContent = `Rs ${rate} × 1 day`;
      summaryTotal.textContent = `Rs ${rate}`;
    }
    if (bookingBarMeta) {
      bookingBarMeta.textContent = prettyDate(selectedDate);
    }
  } else {
    if (priceSummary) {
      priceSummary.hidden = true;
    }
    if (bookingBarMeta) {
      bookingBarMeta.textContent = "Pick a date to book";
    }
  }
}

// Mobile sticky bar: appears past the hero, hides while the form is on screen.
if (bookingBar) {
  const hero = document.querySelector(".hero");
  const bookingSection = document.querySelector("#booking");
  let pastHero = false;
  let formVisible = false;
  const syncBar = () => {
    bookingBar.hidden = !pastHero || formVisible;
  };
  if (hero) {
    new IntersectionObserver(([entry]) => {
      pastHero = !entry.isIntersecting;
      syncBar();
    }).observe(hero);
  }
  if (bookingSection) {
    new IntersectionObserver(([entry]) => {
      formVisible = entry.isIntersecting;
      syncBar();
    }).observe(bookingSection);
  }
}

function renderSkeleton() {
  calendarGrid.innerHTML = "";
  for (let i = 0; i < 35; i += 1) {
    const tile = document.createElement("span");
    tile.className = "day skeleton";
    calendarGrid.appendChild(tile);
  }
}

async function loadAvailability() {
  if (!calendarGrid) {
    return;
  }
  renderSkeleton();

  const firstOfMonth = new Date(viewYear, viewMonth, 1);
  const daysInMonth = new Date(viewYear, viewMonth + 1, 0).getDate();
  calendarRange.textContent = `${MONTH_NAMES[viewMonth]} ${viewYear}`;
  previousDates.disabled = viewYear === today.getFullYear() && viewMonth === today.getMonth();

  let days;
  try {
    const response = await fetch(
      `/api/availability?kayak_id=${selectedKayakId}&start=${isoDate(firstOfMonth)}&days=${daysInMonth}`
    );
    if (!response.ok) {
      throw new Error(`availability ${response.status}`);
    }
    days = await response.json();
  } catch (error) {
    calendarGrid.innerHTML = "<p class=\"calendar-error\">Could not load availability. Please refresh the page.</p>";
    return;
  }

  calendarGrid.innerHTML = "";

  // Monday-first leading blanks so days align with the weekday header.
  const leading = (firstOfMonth.getDay() + 6) % 7;
  for (let i = 0; i < leading; i += 1) {
    const blank = document.createElement("span");
    blank.className = "day blank";
    calendarGrid.appendChild(blank);
  }

  days.forEach((day) => {
    const [y, m, d] = day.date.split("-").map(Number);
    const dateObj = new Date(y, m - 1, d);
    const isPast = dateObj < today;
    const isToday = day.date === isoDate(today);
    const bookable = day.available && !isPast;

    const tile = document.createElement("button");
    tile.type = "button";
    tile.className = "day";
    tile.textContent = String(d);
    if (isToday) {
      tile.classList.add("today");
    }
    if (isPast) {
      tile.classList.add("past");
      tile.disabled = true;
    } else if (!day.available) {
      tile.classList.add("booked");
      tile.disabled = true;
    } else {
      tile.classList.add("available");
    }
    if (day.date === selectedDate) {
      tile.classList.add("selected");
    }
    const prettyFull = dateObj.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
    tile.setAttribute(
      "aria-label",
      `${prettyFull} — ${isPast ? "past" : day.available ? "available" : "booked"}`
    );
    if (bookable) {
      tile.addEventListener("click", () => {
        selectedDate = day.date;
        if (bookingDateInput) {
          bookingDateInput.value = day.date;
          showFieldError(bookingDateInput, "");
        }
        calendarGrid.querySelectorAll(".day.selected").forEach((other) => other.classList.remove("selected"));
        tile.classList.add("selected");
        updateSelectionLine();
      });
    }
    calendarGrid.appendChild(tile);
  });

  updateSelectionLine();
}

function selectKayak(card, options = {}) {
  const changed = selectedKayakId !== card.dataset.kayakId;
  selectedKayakId = card.dataset.kayakId;
  kayakInput.value = selectedKayakId;
  selectedKayakName.textContent = card.dataset.kayakName;
  selectedKayakPrice.textContent = `Rs ${card.dataset.kayakPrice}/day`;
  if (calendarKayakName) {
    calendarKayakName.textContent = card.dataset.kayakName;
  }
  if (changed && !options.keepDate) {
    selectedDate = null;
    if (bookingDateInput) {
      bookingDateInput.value = "";
    }
  }
  updateBookingSummary();

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
  viewMonth -= 1;
  if (viewMonth < 0) {
    viewMonth = 11;
    viewYear -= 1;
  }
  loadAvailability();
});

nextDates?.addEventListener("click", () => {
  viewMonth += 1;
  if (viewMonth > 11) {
    viewMonth = 0;
    viewYear += 1;
  }
  loadAvailability();
});

kayakCards.forEach((card) => {
  card.addEventListener("click", () => selectKayak(card));
});

selectedKayakShortcut?.addEventListener("click", () => {
  document.querySelector("#fleet")?.scrollIntoView({ behavior: "smooth", block: "start" });
});

// The date field is filled from the calendar; clicking it takes you there.
bookingDateInput?.addEventListener("click", () => {
  document.querySelector("#availability")?.scrollIntoView({ behavior: "smooth", block: "start" });
});

// Inline validation: message appears when you leave a field, clears as you fix it.
const fieldValidators = {
  customer_name: (value) => (value.trim() ? "" : "Please enter your full name."),
  customer_email: (value) => (/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value.trim()) ? "" : "Please enter a valid email address."),
  booking_date: (value) => (value ? "" : "Please pick a date from the calendar above."),
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
  if (calendarKayakName && initialCard) {
    calendarKayakName.textContent = initialCard.dataset.kayakName;
  }
  if (initialCard && !initialCard.classList.contains("selected")) {
    selectKayak(initialCard, { scroll: false, keepDate: true });
  } else {
    loadAvailability();
  }
  if (serverError) {
    document.querySelector("#booking")?.scrollIntoView({ block: "start" });
  }
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
