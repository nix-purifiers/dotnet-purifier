// SPDX-License-Identifier: MIT
import java.net.URLClassLoader;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;
public final class Resources {
  public static void main(String[] args) throws Exception {
    try (var loader = new URLClassLoader(new java.net.URL[]{Path.of(args[0]).toUri().toURL()}, null)) {
      try (var stream = Files.walk(Path.of(args[1]))) {
        for (var file : stream.filter(Files::isRegularFile).toList()) {
          var name = Path.of(args[1]).relativize(file).toString();
          try (var resource = loader.getResourceAsStream((args.length > 2 ? args[2] : "") + name)) {
            if (resource == null || !Arrays.equals(resource.readAllBytes(), Files.readAllBytes(file)))
              throw new AssertionError(name);
          }
        }
      }
    }
  }
}
