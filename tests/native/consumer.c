/* SPDX-License-Identifier: MIT */
#include <stdlib.h>
#include "answer.h"
int main(int argc, char **argv) {
  if (argc != 2) return 2;
  int expected = atoi(argv[1]);
  int before = native_collections();
  if (native_answer(0) != expected || native_answer(1) != expected + 2) return 3;
  if (native_collections() - before < 2) return 4;
  return 0;
}
