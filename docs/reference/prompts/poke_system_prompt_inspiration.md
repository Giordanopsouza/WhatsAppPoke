# Poke — System Prompts (inspiração)

Referência dos prompts internos do produto [Poke](https://poke.com) (Interaction Co.), divididos por camada da arquitetura multiagente.

| Arquivo | Papel | Fala com o usuário? |
|---|---|---|
| [poke_execution_agent_prompt.md](./poke_execution_agent_prompt.md) | Motor de execução (`Poke agent.txt`) | Não — devolve dados ao Poke principal |
| [poke_personality_prompt.md](./poke_personality_prompt.md) | Personalidade principal (`Poke_p1` → `Poke_p6`) | Sim — WhatsApp/iMessage/SMS |

## Notas sobre a fonte

- A alteração no README.md original apenas adiciona um link `[**Poke**](./Poke/)` ao índice do repositório — sem conteúdo de prompt.

- Vários nomes de ferramentas no diff aparecem com espaços colapsados (por exemplo, `sendmessageto_agent`, `displaydraft`, `reactto_message`, `querymedia`); foram renderizados com os underscores claramente pretendidos. Onde o diff mostrava placeholders de tags vazias (`< >`), os nomes reais das tags foram removidos na página renderizada, então a função foi descrita em vez de inventar nomes de tags.
