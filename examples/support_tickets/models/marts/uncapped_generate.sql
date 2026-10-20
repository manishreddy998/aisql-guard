{{ config(materialized='table') }}

select
  ticket_id,
  AI.GENERATE(
    concat('Summarize this ticket in one sentence: ', body)
  ).result as summary
from {{ ref('stg_tickets') }}
where status = 'closed'
