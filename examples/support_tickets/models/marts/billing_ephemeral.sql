{{ config(materialized='ephemeral') }}

select
  ticket_id,
  AI.IF(
    ('Is this ticket about billing? ', body),
    model_params => JSON '{"generation_config":{"thinking_config":{"thinking_budget":0}}}'
  ) as is_billing
from {{ ref('stg_tickets') }}
where priority = 'high'
