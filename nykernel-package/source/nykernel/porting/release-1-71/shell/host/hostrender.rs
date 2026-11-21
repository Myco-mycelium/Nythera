// Host driver: renders the shell (the SAME ui.rs the kernel runs) at W x H, prints "<hash> <class> <cols> <rows> <tiles>" and optionally writes a PPM.
#![allow(dead_code)]
#[path = "../src/font_data.rs"] mod font_data;
#[path = "../src/ui.rs"] mod ui;
use std::io::Write;
fn main() {
    let a: Vec<String> = std::env::args().collect();
    let (w, h): (i32, i32) = (a[1].parse().unwrap(), a[2].parse().unwrap());
    // "lock" renders the lock screen (dark), which is what `nyrqis.lock=1`
    // puts on the display: the host tool and the kernel compile the same source,
    // so the screen can be reviewed before it is booted.
    // Dark is the boot default; "light" is the only argument that asks for the other palette.
    let theme = if a[3] == "light" { ui::Theme::Light } else { ui::Theme::Dark };
    // The third argument names what to draw: "lock", or a Tide posture ("tablet", "phone", "console").
    // "dark"/"light" render the desktop posture. The kernel passes the same posture the device mode chose.
    let posture = match a[3].as_str() {
        "tablet" => Some(ui::Posture::Tablet),
        "phone" => Some(ui::Posture::Phone),
        "console" => Some(ui::Posture::Console),
        _ => Some(ui::Posture::Desktop),
    };
    let mut buf = vec![0u32; (w * h) as usize];
    let mut fb = ui::Fb { base: buf.as_mut_ptr(), pitch: w as usize, w, h };
    let info = if a[3] == "lock" {
        ui::render_lockscreen(&mut fb, theme)
    } else {
        ui::render_posture(&mut fb, theme, posture.unwrap())
    };
    println!("{:#x} {:?} {} {} {}", ui::hash(&fb), info.class, info.cols, info.rows, info.tiles);
    if a.len() > 4 {
        let mut f = std::fs::File::create(&a[4]).unwrap(); write!(f, "P6\n{} {}\n255\n", w, h).unwrap();
        for p in &buf { f.write_all(&[(p >> 16) as u8, (p >> 8) as u8, *p as u8]).unwrap(); }
    }
}
