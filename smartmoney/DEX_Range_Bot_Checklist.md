# DEX Range Bot Checklist

- Рынок реально в диапазоне, а не в тренде?
- Есть понятные `lower / upper` границы?
- Это не середина диапазона?
- `Slippage`, `price impact` и газ в норме?
- В пуле хватает ликвидности?
- Токен не выглядит как `tax / reflection / honeypot-like`?
- Диапазон ещё не сломан `kill-switch`-условием?
- Для live-режима проверены approvals и wallet executor?

Если `2+` ответов грязные, новый лот не открывать.
