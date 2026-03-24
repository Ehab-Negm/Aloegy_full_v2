# AloEgy — Frontend

React-based dashboard and customer-facing interface for the AloEgy voice ordering platform.

## Tech Stack

- **React 18** with TypeScript
- **Vite** for fast builds and HMR
- **Tailwind CSS** + **shadcn/ui** for styling
- **Recharts** for analytics charts
- **livekit-client** for real-time voice sessions
- **React Router** for client-side routing

## Pages

| Page | Path | Description |
|---|---|---|
| Landing | `/` | Product landing page with demo CTA |
| Login | `/login` | OTP-based phone authentication |
| Dashboard | `/dashboard` | Restaurant owner dashboard — orders, analytics, settings |
| Admin | `/admin` | Platform admin — manage all restaurants and users |
| Sales | `/sales` | Sales team — leads, onboarding, pipeline |
| Pricing | `/pricing` | Subscription plans |

## Getting Started

```bash
npm install
npm run dev
```

Runs on `http://localhost:8080` by default.

## Build

```bash
npm run build
npm run preview    # preview production build
```

## Environment

The frontend connects to the backend at `http://127.0.0.1:8000` by default. To change this, update the `API_BASE` constant in `src/services/api.ts`.
