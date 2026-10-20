select
  ticket_id,
  body,
  status,
  priority,
  updated_at
from {{ source('raw', 'tickets') }}
