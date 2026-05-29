import { useParams, Link } from 'react-router-dom'
import { Card } from '../components/Card'

// Placeholder — the live home detail view is built in Task 6.
export function HomeDetail() {
  const { id } = useParams()
  return (
    <div>
      <Link to="/" className="text-[13px] text-accent">
        ← Fleet overview
      </Link>
      <h1 className="mb-4 mt-2 text-[22px] font-medium text-text">Home {id}</h1>
      <Card>
        <p className="text-[14px] text-text-muted">
          Live power tiles, SoC ring, thermostat, and circuit bars arrive in Task 6.
        </p>
      </Card>
    </div>
  )
}
