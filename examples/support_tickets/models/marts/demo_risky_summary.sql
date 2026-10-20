select
  AI.GENERATE(
    (select as struct src.*)
  ).result as summary
from {{ ref('stg_tickets') }} as src
