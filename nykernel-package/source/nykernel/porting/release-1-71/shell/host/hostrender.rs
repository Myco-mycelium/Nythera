// Host driver: renders the shell (the SAME ui.rs the kernel runs) at W x H, prints "<hash> <class> <cols> <rows> <tiles>" and optionally writes a PPM.
#![allow(dead_code)]
#[path = "../src/font_data.rs"] mod font_data;
#[path = "../src/ui.rs"] mod ui;
use std::io::Write;
fn main() {
    let a: Vec<String> = std::env::args().collect();
    let (w, h): (i32, i32) = (a[1].parse().unwrap(), a[2].parse().unwrap());
    let theme = if a[3] == "dark" { ui::Theme::Dark } else { ui::Theme::Light };
    let mut buf = vec![0u32; (w * h) as usize];
    let mut fb = ui::Fb { base: buf.as_mut_ptr(), pitch: w as usize, w, h };
    let info = ui::render(&mut fb, theme);
    println!("{:#x} {:?} {} {} {}", ui::hash(&fb), info.class, info.cols, info.rows, info.tiles);
    if a.len() > 4 {
        let mut f = std::fs::File::create(&a[4]).unwrap(); write!(f, "P6\n{} {}\n255\n", w, h).unwrap();
        for p in &buf { f.write_all(&[(p >> 16) as u8, (p >> 8) as u8, *p as u8]).unwrap(); }
    }
}
