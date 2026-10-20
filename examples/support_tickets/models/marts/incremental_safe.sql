{{ config(materialized='incremental', unique_key='ticket_id') }}

select
  src.ticket_id,
  AI.GENERATE(
    concat('Summarize this ticket in one sentence: ', src.body),
    model_params => JSON '{"generation_config":{"max_output_tokens":128,"thinking_config":{"thinking_level":"LOW"}}}'
  ).result as summary
from {{ ref('stg_tickets') }} as src
where src.status = 'closed'
{% if is_incremental() %}
  and not exists (
    select 1
    from {{ this }} as already
    where already.ticket_id = src.ticket_id
      and already.summary is not null
  )
{% endif %}
