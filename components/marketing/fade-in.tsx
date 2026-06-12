"use client";

import { motion, type HTMLMotionProps } from "framer-motion";

interface FadeInProps extends HTMLMotionProps<"div"> {
  delay?: number;
}

/** Shared scroll-reveal wrapper: subtle fade-up, fires once. */
export function FadeIn({ delay = 0, children, ...props }: FadeInProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-60px" }}
      transition={{ duration: 0.5, ease: "easeOut", delay }}
      {...props}
    >
      {children}
    </motion.div>
  );
}
